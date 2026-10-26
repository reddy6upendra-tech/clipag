import re
import csv
import json
import zipfile
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import Config
from app.llm.client import ClipCandidate
from app.pipeline.reframer import analyze_clip_reframing
from app.pipeline.captions import generate_ass_subtitles, escape_ffmpeg_path

logger = logging.getLogger("clipag.renderer")

def slugify(text: str) -> str:
    """Converts a title into a clean, filesystem-friendly slug."""
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    s = re.sub(r"[-\s]+", "_", s)
    return s[:30] if s else "reel"

def render_single_clip(
    video_path: Path,
    clip: ClipCandidate,
    clip_idx: int,
    output_dir: Path,
    words: List[Dict[str, Any]],
    language: Optional[str] = None,
    caption_style: str = "bold_yellow",
    caption_position: str = "bottom",
    font_size_override: Optional[int] = None,
    force_recompute: bool = False,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Renders one 9:16 vertical clip with smoothed reframing, animated ASS captions,
    loudnorm audio leveling (-14 LUFS), natural speech breathing buffers, and clean transitions.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(clip.title)
    filename = f"clip_{clip_idx:02d}_{slug}.mp4"
    out_video_path = output_dir / filename
    meta_path = metadata_dir / f"clip_{clip_idx:02d}.json"
    ass_path = output_dir / f"clip_{clip_idx:02d}.ass"

    # Add natural speech breathing room (0.10s lead-in, 0.25s trail-out)
    render_start = max(0.0, round(clip.start - 0.10, 3))
    render_end = round(clip.end + 0.25, 3)
    duration = max(1.0, round(render_end - render_start, 3))

    # Check cache
    if not force_recompute and out_video_path.exists() and meta_path.exists():
        logger.info(f"Clip {clip_idx} cache hit ({filename}).")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["video_path"] = str(out_video_path)
        return meta

    if progress_callback:
        progress_callback(f"Analyzing face tracking for Clip {clip_idx}...", None)

    # 1. Reframing analysis
    reframe = analyze_clip_reframing(
        video_path=video_path,
        start_sec=render_start,
        end_sec=render_end,
        sample_fps=Config.SAMPLE_FPS,
        min_speaker_hold_sec=Config.MIN_SPEAKER_HOLD_SEC,
    )

    # 2. Generate animated ASS subtitles
    if progress_callback:
        progress_callback(f"Generating animated subtitles for Clip {clip_idx}...", None)
    generate_ass_subtitles(
        words=words,
        clip_start=render_start,
        clip_end=render_end,
        output_ass_path=ass_path,
        style_name=caption_style,
        language=language,
        position=caption_position,
        font_size_override=font_size_override,
    )

    # 3. Assemble FFmpeg filter graph
    escaped_ass = escape_ffmpeg_path(ass_path)
    escaped_fonts = escape_ffmpeg_path(Config.FONTS_DIR)
    reframe_filter = reframe["filter_string"]

    # Subtle video transition
    video_fade_d = min(0.2, duration / 6.0)
    video_fade_out_st = max(0.0, duration - video_fade_d)

    # Studio-grade 50ms audio micro-fade: eliminates cut pops without muting opening syllables
    audio_fade_d = 0.05
    audio_fade_out_st = max(0.0, duration - audio_fade_d)

    vf = (
        f"{reframe_filter},"
        f"subtitles='{escaped_ass}':fontsdir='{escaped_fonts}',"
        f"fade=t=in:st=0:d={video_fade_d},"
        f"fade=t=out:st={video_fade_out_st}:d={video_fade_d}"
    )

    af = (
        f"afade=t=in:ss=0:d={audio_fade_d},"
        f"afade=t=out:st={audio_fade_out_st}:d={audio_fade_d},"
        f"loudnorm=I={Config.TARGET_LUFS}:LRA=11:TP=-1.5:linear=true"
    )

    if progress_callback:
        progress_callback(f"Rendering Clip {clip_idx}: {clip.title[:25]}...", None)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(render_start),
        "-t", str(duration),
        "-i", str(video_path),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264",
        "-preset", Config.PRESET,
        "-crf", "22",
        "-r", str(Config.FPS),
        "-pix_fmt", "yuv420p",
        "-threads", "0",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        str(out_video_path),
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        logger.error(f"FFmpeg render error for Clip {clip_idx}: {proc.stderr}")
        raise RuntimeError(f"FFmpeg failed rendering clip {clip_idx}: {proc.stderr}")

    # Write per-reel metadata
    metadata = {
        "clip_index": clip_idx,
        "filename": filename,
        "title": clip.title,
        "hook_line": clip.hook_line,
        "reason": clip.reason,
        "score": clip.score,
        "hashtags": clip.suggested_hashtags,
        "description": clip.description,
        "start_sec": clip.start,
        "end_sec": clip.end,
        "duration_sec": duration,
        "video_path": str(out_video_path),
        "reframe_mode": reframe.get("mode", "crop"),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return metadata

def package_reels(output_dir: Path, rendered_clips: List[Dict[str, Any]]) -> Path:
    """Creates a combined CSV summary and zips all reels and metadata."""
    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    csv_path = metadata_dir / "reels_summary.csv"
    zip_path = output_dir / "all_reels_package.zip"

    # 1. Write combined CSV
    fieldnames = [
        "clip_index", "filename", "title", "hook_line", "reason",
        "score", "hashtags", "description", "start_sec", "end_sec", "duration_sec"
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in rendered_clips:
            writer.writerow({
                "clip_index": c.get("clip_index"),
                "filename": c.get("filename"),
                "title": c.get("title"),
                "hook_line": c.get("hook_line"),
                "reason": c.get("reason"),
                "score": c.get("score"),
                "hashtags": " ".join(c.get("hashtags", [])),
                "description": c.get("description"),
                "start_sec": c.get("start_sec"),
                "end_sec": c.get("end_sec"),
                "duration_sec": c.get("duration_sec"),
            })

    # 2. Create Zip archive
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for c in rendered_clips:
            v_path = Path(c["video_path"])
            if v_path.exists():
                zipf.write(v_path, arcname=v_path.name)
            meta_json = metadata_dir / f"clip_{c['clip_index']:02d}.json"
            if meta_json.exists():
                zipf.write(meta_json, arcname=f"metadata/{meta_json.name}")
        if csv_path.exists():
            zipf.write(csv_path, arcname="metadata/reels_summary.csv")

    logger.info(f"Packaged {len(rendered_clips)} reels into {zip_path.name} ({zip_path.stat().st_size} bytes).")
    return zip_path
