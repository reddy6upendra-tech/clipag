import subprocess
import json
import pytest
from pathlib import Path

@pytest.fixture(scope="session")
def cli_test_video(tmp_path_factory) -> Path:
    """Creates a 125-second synthetic video with spoken sentences for CLI end-to-end testing."""
    temp_dir = tmp_path_factory.mktemp("cli_media")
    video_path = temp_dir / "long_sample.mp4"
    
    # Generate 125-second video with continuous looped speech
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "flite=text='Welcome to AI Reels Clipper. This is a complete test of speech transcription and vertical video reframing.':voice=kal",
        "-filter_complex", "[1:a]aloop=loop=-1:size=2e+09[a]",
        "-map", "0:v",
        "-map", "[a]",
        "-t", "125",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        str(video_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return video_path

def test_cli_end_to_end_produces_5_plus_reels(cli_test_video, tmp_path):
    cmd = [
        "python", "-m", "app.cli",
        "--input", str(cli_test_video),
        "--clips", "5",
        "--model", "tiny",
        "--force",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.returncode == 0, f"CLI pipeline execution failed: {proc.stderr}\nOutput: {proc.stdout}"
    
    # Find generated output directory
    from app.config import Config
    from app.pipeline.ingest import compute_file_hash
    
    job_id = compute_file_hash(cli_test_video)[:16]
    out_dir = Config.OUTPUTS_DIR / job_id
    assert out_dir.exists(), f"Output directory {out_dir} does not exist"
    
    # Check rendered MP4 files
    mp4s = list(out_dir.glob("clip_*.mp4"))
    assert len(mp4s) >= 5, f"Expected at least 5 clips, got {len(mp4s)}"
    
    # Check resolution of rendered clips (1080x1920)
    for mp4 in mp4s:
        assert mp4.stat().st_size > 1000
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(mp4),
        ]
        res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        assert res.stdout.strip() == "1080x1920"

    # Check metadata files
    meta_dir = out_dir / "metadata"
    assert (meta_dir / "reels_summary.csv").exists()
    jsons = list(meta_dir.glob("clip_*.json"))
    assert len(jsons) >= 5

    # Check zip archive
    zip_pkg = out_dir / "all_reels_package.zip"
    assert zip_pkg.exists()
    assert zip_pkg.stat().st_size > 5000
