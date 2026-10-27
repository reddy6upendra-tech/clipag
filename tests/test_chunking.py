import wave
import pytest
import subprocess
from pathlib import Path
from app.pipeline.transcribe import slice_audio_into_chunks, transcribe_audio

@pytest.fixture(scope="session")
def multi_minute_audio(tmp_path_factory) -> Path:
    """Generates a synthetic 400-second (6.6-minute) test WAV with spoken-like periodic tones."""
    temp_dir = tmp_path_factory.mktemp("chunk_audio")
    wav_path = temp_dir / "long_sample.wav"
    
    # Generate 400s audio with tone and silence pauses
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=400",
        "-ar", "16000",
        "-ac", "1",
        str(wav_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return wav_path

def test_slice_audio_into_chunks(multi_minute_audio, tmp_path):
    chunks_dir = tmp_path / "test_chunks"
    chunks = slice_audio_into_chunks(
        audio_path=multi_minute_audio,
        chunks_dir=chunks_dir,
        chunk_seconds=180.0, # Test 3-minute chunks
        silence_gaps=[{"start": 178.0, "duration": 1.2}],
    )
    assert len(chunks) >= 2
    assert chunks[0]["start"] == 0.0
    # Boundary should be aligned near 178.6
    assert 170.0 <= chunks[0]["end"] <= 190.0
    assert chunks[1]["start"] == chunks[0]["end"]
    for c in chunks:
        assert Path(c["path"]).exists()
