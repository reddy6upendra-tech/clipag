import subprocess
import pytest
from pathlib import Path
from app.pipeline.captions import generate_ass_subtitles, format_ass_timestamp
from app.config import Config

def test_ass_timestamp_formatting():
    assert format_ass_timestamp(0.0) == "0:00:00.00"
    assert format_ass_timestamp(65.45) == "0:01:05.45"
    assert format_ass_timestamp(3605.12) == "1:00:05.12"

def test_caption_timing_sync(tmp_path):
    words = [
        {"word": "Welcome", "start": 10.0, "end": 10.4},
        {"word": "to", "start": 10.45, "end": 10.6},
        {"word": "Reels", "start": 10.65, "end": 11.2},
    ]
    clip_start = 10.0
    clip_end = 12.0
    ass_path = tmp_path / "test_timing.ass"
    
    generate_ass_subtitles(words, clip_start, clip_end, ass_path, style_name="bold_yellow", language="en")
    assert ass_path.exists()
    
    content = ass_path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "WELCOME" in content
    
    # Check that events start around 0:00:00.00 and stay within 100ms of relative timestamps
    lines = [l for l in content.splitlines() if l.startswith("Dialogue:")]
    assert len(lines) == 3
    # First word starts at 0.0s (10.0 - 10.0)
    assert lines[0].startswith("Dialogue: 0,0:00:00.00")
    # Second word starts at 0.45s (10.45 - 10.0)
    assert "0:00:00.45" in lines[1]
    # Highlight tag present
    assert "\\c" in lines[0]

def test_multilingual_indian_scripts_render(tmp_path):
    # Telugu test
    telugu_words = [
        {"word": "నమస్కారం", "start": 0.0, "end": 1.0},
        {"word": "స్వాగతం", "start": 1.1, "end": 2.0},
    ]
    ass_telugu = tmp_path / "telugu.ass"
    generate_ass_subtitles(telugu_words, 0.0, 2.0, ass_telugu, language="te")
    telugu_txt = ass_telugu.read_text(encoding="utf-8")
    assert "Noto Sans Telugu" in telugu_txt
    assert "నమస్కారం" in telugu_txt

    # Hindi test
    hindi_words = [
        {"word": "नमस्ते", "start": 0.0, "end": 1.0},
        {"word": "मित्रों", "start": 1.1, "end": 2.0},
    ]
    ass_hindi = tmp_path / "hindi.ass"
    generate_ass_subtitles(hindi_words, 0.0, 2.0, ass_hindi, language="hi")
    hindi_txt = ass_hindi.read_text(encoding="utf-8")
    assert "Noto Sans Devanagari" in hindi_txt
    assert "नमस्ते" in hindi_txt

    # Burn-in test using FFmpeg with Telugu ASS subtitle on a 1080x1920 canvas
    out_mp4 = tmp_path / "telugu_rendered.mp4"
    from app.pipeline.captions import escape_ffmpeg_path
    escaped_ass = escape_ffmpeg_path(ass_telugu)
    escaped_fonts = escape_ffmpeg_path(Config.FONTS_DIR)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=1",
        "-vf", f"subtitles='{escaped_ass}':fontsdir='{escaped_fonts}'",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        str(out_mp4),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.returncode == 0, f"FFmpeg burn-in failed: {proc.stderr}"
    assert out_mp4.exists()
