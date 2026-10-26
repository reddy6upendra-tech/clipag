import time
import subprocess
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.api.server import app, JOBS

client = TestClient(app)

@pytest.fixture(scope="session")
def api_test_video(tmp_path_factory) -> Path:
    temp_dir = tmp_path_factory.mktemp("api_media")
    video_path = temp_dir / "api_sample.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "flite=text='This is a quick test of the AI Reels API server.':voice=kal",
        "-filter_complex", "[1:a]aloop=loop=-1:size=2e+09[a]",
        "-map", "0:v",
        "-map", "[a]",
        "-t", "45",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        str(video_path),
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return video_path

def test_static_html_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AI Reels Clipper" in resp.text
    assert "Notice:" in resp.text

def test_api_config():
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "caption_styles" in data
    assert "bold_yellow" in data["caption_styles"]

def test_api_doctor():
    resp = client.get("/api/doctor")
    assert resp.status_code == 200
    data = resp.json()
    assert "ffmpeg" in data
    assert data["ffmpeg"] is True

def test_job_submission_and_progress(api_test_video):
    with open(api_test_video, "rb") as f:
        resp = client.post(
            "/api/jobs",
            data={
                "clips": "5",
                "style": "bold_yellow",
                "model": "tiny",
            },
            files={"file": ("api_sample.mp4", f, "video/mp4")},
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # Poll until completed or timeout (rendering 5 vertical 1080x1920 reels on CPU takes ~3-4 mins)
    max_wait = 360 # seconds
    start_t = time.time()
    completed = False
    result_data = None

    while time.time() - start_t < max_wait:
        st_resp = client.get(f"/api/jobs/{job_id}")
        assert st_resp.status_code == 200
        st = st_resp.json()
        if st["status"] == "completed":
            completed = True
            result_data = st["result"]
            break
        elif st["status"] == "failed":
            pytest.fail(f"Job failed: {st.get('error')}")
        time.sleep(1.0)

    assert completed is True
    assert result_data is not None
    assert len(result_data["clips"]) >= 5

    # Test download-zip endpoint
    content_hash_id = result_data["content_hash_id"]
    zip_resp = client.get(f"/api/jobs/{content_hash_id}/download-zip")
    assert zip_resp.status_code == 200
    assert zip_resp.headers["content-type"] == "application/zip"

    # Test adjust endpoint
    adj_resp = client.post(
        f"/api/jobs/{content_hash_id}/adjust",
        json={
            "clip_index": 1,
            "start_delta": 1.0,
            "end_delta": -1.0,
            "caption_style": "bold_yellow",
        },
    )
    assert adj_resp.status_code == 200
    updated_clip = adj_resp.json()
    assert updated_clip["clip_index"] == 1
    assert "video_url" in updated_clip
