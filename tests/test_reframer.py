import cv2
import numpy as np
import subprocess
import pytest
from pathlib import Path
from app.pipeline.reframer import smooth_trajectory, build_ffmpeg_crop_filter, build_blurred_fit_filter, analyze_clip_reframing

def test_smooth_trajectory_clamps_max_speed():
    timestamps = [0.0, 0.33, 0.66, 1.0, 1.33]
    # Extreme jump from 100 to 1200 px
    raw = [100.0, 1200.0, 1200.0, 1200.0, 1200.0]
    max_speed = 200.0 # px/s
    smoothed = smooth_trajectory(timestamps, raw, window_size=3, max_speed_px_per_sec=max_speed, min_bound=0, max_bound=1920)
    
    assert len(smoothed) == len(raw)
    for i in range(1, len(smoothed)):
        dt = timestamps[i] - timestamps[i - 1]
        delta = abs(smoothed[i] - smoothed[i - 1])
        # Delta must never exceed max_speed * dt (+ small epsilon)
        assert delta <= (max_speed * dt) + 1.0

def test_crop_filter_syntax():
    filt = build_ffmpeg_crop_filter(
        in_w=1920,
        in_h=1080,
        crop_w=608,
        crop_h=1080,
        timestamps=[0.0, 1.0, 2.0],
        crop_x_coords=[100, 200, 300],
    )
    assert "scale=1080:1920" in filt
    assert "crop=" in filt

def test_blurred_fit_filter_syntax():
    filt = build_blurred_fit_filter(in_w=1920, in_h=1080)
    assert "scale=1080:1920" in filt
    assert "boxblur=" in filt
    assert "overlay=" in filt

def test_render_sample_clips_1080x1920(tmp_path):
    """
    Renders 3 sample clips to 1080x1920:
    1. Left crop
    2. Right crop
    3. Blurred fit (no face)
    Checks resulting resolution using ffprobe.
    """
    test_src = tmp_path / "source.mp4"
    # Create 3-second 1280x720 video
    cmd_gen = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
        "-t", "3",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        str(test_src),
    ]
    subprocess.run(cmd_gen, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    
    filters = [
        # Clip 1: Crop focused on left (x=100)
        "crop=405:720:100:0,scale=1080:1920,setsar=1",
        # Clip 2: Crop focused on right (x=700)
        "crop=405:720:700:0,scale=1080:1920,setsar=1",
        # Clip 3: Blurred fit layout (no face)
        build_blurred_fit_filter(1280, 720),
    ]

    for idx, f in enumerate(filters):
        out_clip = tmp_path / f"clip_{idx}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(test_src),
            "-vf", f,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            str(out_clip),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.returncode == 0, f"FFmpeg failed: {proc.stderr}"
        
        # Verify output resolution is exactly 1080x1920
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(out_clip),
        ]
        res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        assert res.stdout.strip() == "1080x1920"


def test_face_tracker_resilience(monkeypatch):
    from app.pipeline.reframer import FaceTracker
    tracker = FaceTracker()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    faces = tracker.detect_faces(frame, 100, 100)
    assert isinstance(faces, list)
