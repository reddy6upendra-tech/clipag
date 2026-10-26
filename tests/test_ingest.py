import subprocess
import json
import pytest
from pathlib import Path
from app.pipeline.ingest import ingest_source, get_video_metadata, compute_file_hash

@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """Creates a fast, synthetic 5-second 1280x720 video with 1kHz tone for testing."""
    temp_dir = tmp_path_factory.mktemp("media")
    video_path = temp_dir / "synthetic_test.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
        "-t", "5",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-c:a", "aac",
        str(video_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return video_path

def test_metadata_extraction(sample_video):
    meta = get_video_metadata(sample_video)
    assert meta["width"] == 1280
    assert meta["height"] == 720
    assert 4.8 <= meta["duration"] <= 5.2
    assert meta["fps"] == 30.0

def test_ingest_local_file_and_cache(sample_video, tmp_path):
    work_dir = tmp_path / "work"
    
    # 1. First ingest run
    result1 = ingest_source(str(sample_video), work_dir=work_dir)
    assert result1["cached"] is False
    assert Path(result1["audio_path"]).exists()
    assert Path(result1["metadata_path"]).exists()
    assert result1["metadata"]["width"] == 1280
    
    # Verify audio properties (16000 Hz, 1 channel)
    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "json",
        result1["audio_path"],
    ]
    proc = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    audio_info = json.loads(proc.stdout)
    stream = audio_info["streams"][0]
    assert stream["sample_rate"] == "16000"
    assert stream["channels"] == 1
    
    # 2. Second ingest run (should hit cache)
    result2 = ingest_source(str(sample_video), work_dir=work_dir)
    assert result2["cached"] is True
    assert result2["job_id"] == result1["job_id"]
