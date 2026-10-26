import pytest
import subprocess
from pathlib import Path
from app.pipeline.ingest import ingest_source
from app.pipeline.transcribe import transcribe_audio

def test_failure_invalid_youtube_url():
    with pytest.raises(RuntimeError) as exc_info:
        ingest_source("https://www.youtube.com/watch?v=non_existent_fake_video_12345")
    err = str(exc_info.value).lower()
    assert "video" in err or "failed" in err or "unavailable" in err

def test_failure_too_short_video(tmp_path):
    # Create a 1-second video
    short_vid = tmp_path / "too_short.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
        "-t", "1",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        str(short_vid),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    with pytest.raises(RuntimeError) as exc_info:
        ingest_source(str(short_vid), work_dir=tmp_path / "work")
    assert "too short" in str(exc_info.value).lower()

def test_failure_no_speech_audio(tmp_path):
    # Create a 10-second pure sine wave audio (music/tone, zero speech)
    tone_wav = tmp_path / "tone.wav"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-ar", "16000",
        "-ac", "1",
        str(tone_wav),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    with pytest.raises(RuntimeError) as exc_info:
        transcribe_audio(
            audio_path=tone_wav,
            job_dir=tmp_path / "job_tone",
            model_name="tiny",
            force_recompute=True,
        )
    assert "no speech detected" in str(exc_info.value).lower()
