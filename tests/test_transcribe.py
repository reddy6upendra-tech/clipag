import subprocess
import json
import pytest
from pathlib import Path
from app.pipeline.transcribe import transcribe_audio

@pytest.fixture(scope="session")
def sample_speech_wav(tmp_path_factory) -> Path:
    temp_dir = tmp_path_factory.mktemp("audio")
    wav_path = temp_dir / "flite_speech.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "flite=text='This is an automated test of the AI speech recognition system. Everything is working as expected.':voice=kal",
        "-ar", "16000",
        "-ac", "1",
        str(wav_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return wav_path

def test_transcription_and_cache(sample_speech_wav, tmp_path):
    job_dir = tmp_path / "whisper_test_job"
    
    # Run 1: Transcribe with tiny model for fast unit testing
    result1 = transcribe_audio(sample_speech_wav, job_dir=job_dir, model_name="tiny")
    
    assert result1["cached"] is False
    assert (job_dir / "transcript.json").exists()
    assert (job_dir / "transcript.txt").exists()
    assert (job_dir / "audio_energy.json").exists()
    
    # Check structure
    assert len(result1["segments"]) > 0
    assert len(result1["words"]) > 0
    first_word = result1["words"][0]
    assert "word" in first_word
    assert "start" in first_word
    assert "end" in first_word
    assert first_word["end"] >= first_word["start"]
    
    # Run 2: Cached run
    result2 = transcribe_audio(sample_speech_wav, job_dir=job_dir, model_name="tiny")
    assert result2["cached"] is True
    assert len(result2["words"]) == len(result1["words"])
