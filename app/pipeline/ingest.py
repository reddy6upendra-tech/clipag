import os
import re
import json
import hashlib
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from app.config import Config

logger = logging.getLogger("clipag.ingest")

def compute_file_hash(filepath: Path, chunk_size: int = 1024 * 1024) -> str:
    """Computes SHA-256 hash of a local file."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(chunk_size):
            sha.update(chunk)
    return sha.hexdigest()

def compute_url_hash(url: str) -> str:
    """Computes SHA-256 hash for a URL."""
    clean_url = url.strip()
    return hashlib.sha256(clean_url.encode("utf-8")).hexdigest()

def is_url(path_or_url: str) -> bool:
    """Check if the input string is a web URL."""
    p = path_or_url.strip()
    return bool(re.match(r"^https?://", p, re.IGNORECASE))

def get_video_metadata(video_path: Path) -> Dict[str, Any]:
    """Extracts video metadata (duration, resolution, fps, etc.) using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=width,height,codec_type,r_frame_rate,avg_frame_rate,codec_name:format=duration,size,bit_rate",
        "-of", "json",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(proc.stdout)
    except Exception as e:
        logger.error(f"Failed to inspect video with ffprobe: {e}")
        raise RuntimeError(f"Could not read video metadata from {video_path}: {e}")

    duration = float(data.get("format", {}).get("duration", 0.0))
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if "width" in s and "height" in s), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    codec_name = video_stream.get("codec_name", "unknown")
    
    # Calculate FPS
    fps_str = video_stream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 30.0
    else:
        fps = float(fps_str) if fps_str else 30.0

    meta = {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "codec_name": codec_name,
        "file_size": int(data.get("format", {}).get("size", 0)),
        "filename": video_path.name,
        "has_audio": has_audio,
    }
    return meta

def extract_audio(video_path: Path, output_wav_path: Path, sample_rate: int = 16000) -> Path:
    """Extracts 16 kHz mono PCM WAV audio using ffmpeg."""
    output_wav_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        str(output_wav_path),
    ]
    logger.info(f"Extracting 16kHz mono audio from {video_path.name} -> {output_wav_path.name}...")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        logger.error(f"FFmpeg audio extraction failed: {proc.stderr}")
        raise RuntimeError(f"FFmpeg audio extraction failed: {proc.stderr}")
    return output_wav_path

def download_video(url: str, dest_dir: Path) -> Path:
    """Downloads best quality MP4 up to 1080p using yt-dlp with clear error diagnostics."""
    import yt_dlp
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest_dir / "source.%(ext)s")
    
    ydl_opts = {
        "format": "bestvideo[vcodec^=avc][height<=1080]+bestaudio[ext=m4a]/bestvideo[vcodec^=h264][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "outtmpl": out_tmpl,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": True,
    }
    logger.info(f"Downloading video from {url}...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as de:
        err_str = str(de).lower()
        if "private video" in err_str:
            raise RuntimeError("This YouTube video is private. Please provide a public or unlisted video link.")
        elif "sign in to confirm your age" in err_str or "age-restricted" in err_str:
            raise RuntimeError("This YouTube video is age-restricted and cannot be downloaded without authentication.")
        elif "video unavailable" in err_str or "deleted" in err_str or "not found" in err_str:
            raise RuntimeError("This YouTube video is unavailable or has been deleted. Please check the URL.")
        elif "playlist" in err_str:
            raise RuntimeError("Playlist URLs are not supported. Please provide a link to a single video.")
        else:
            raise RuntimeError(f"Failed to download video: {str(de)}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error downloading video: {e}")
    
    target_mp4 = dest_dir / "source.mp4"
    if target_mp4.exists():
        return target_mp4
    
    # Check for any source.* file
    matches = list(dest_dir.glob("source.*"))
    if matches:
        return matches[0]
    
    raise FileNotFoundError(f"yt-dlp completed but output file not found in {dest_dir}")

def ingest_source(
    source: str,
    work_dir: Optional[Path] = None,
    force_recompute: bool = False,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Ingests either a URL or a local file path.
    Extracts 16 kHz mono WAV and saves metadata.json.
    Supports caching by content / URL hash.
    """
    base_work_dir = work_dir or Config.WORK_DIR
    base_work_dir.mkdir(parents=True, exist_ok=True)
    
    if progress_callback:
        progress_callback("Ingesting video source...", 5)

    if is_url(source):
        url_hash = compute_url_hash(source)[:16]
        job_dir = base_work_dir / url_hash
        job_dir.mkdir(parents=True, exist_ok=True)
        video_path = job_dir / "source.mp4"
        audio_path = job_dir / "audio.wav"
        metadata_path = job_dir / "metadata.json"
        
        # Check cache
        if not force_recompute and video_path.exists() and audio_path.exists() and metadata_path.exists():
            logger.info(f"Ingest cache hit for URL: {source} (job_id: {url_hash})")
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            return {
                "job_id": url_hash,
                "job_dir": str(job_dir),
                "video_path": str(video_path),
                "audio_path": str(audio_path),
                "metadata_path": str(metadata_path),
                "metadata": metadata,
                "cached": True,
            }
        
        # Download
        if progress_callback:
            progress_callback(f"Downloading from {source[:30]}...", 10)
        downloaded_video = download_video(source, job_dir)
        if downloaded_video != video_path:
            shutil.move(str(downloaded_video), str(video_path))
    else:
        local_path = Path(source).resolve()
        if not local_path.exists():
            raise FileNotFoundError(f"Input file not found: {source}")
        
        file_hash = compute_file_hash(local_path)[:16]
        job_dir = base_work_dir / file_hash
        job_dir.mkdir(parents=True, exist_ok=True)
        video_path = job_dir / f"source{local_path.suffix}"
        audio_path = job_dir / "audio.wav"
        metadata_path = job_dir / "metadata.json"
        
        # Check cache
        if not force_recompute and video_path.exists() and audio_path.exists() and metadata_path.exists():
            logger.info(f"Ingest cache hit for local file: {local_path.name} (job_id: {file_hash})")
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            return {
                "job_id": file_hash,
                "job_dir": str(job_dir),
                "video_path": str(video_path),
                "audio_path": str(audio_path),
                "metadata_path": str(metadata_path),
                "metadata": metadata,
                "cached": True,
            }
        
        if not video_path.exists() or video_path.resolve() != local_path:
            shutil.copy2(str(local_path), str(video_path))

    # Extract metadata first
    metadata = get_video_metadata(video_path)
    if metadata.get("duration", 0.0) < 3.0:
        raise RuntimeError(f"Video duration ({metadata.get('duration', 0.0):.1f}s) is too short. Videos must be at least 3 seconds long to process.")
    if not metadata.get("has_audio", True):
        raise RuntimeError("Video has no audio track. AI Reels Clipper requires spoken dialogue to extract viral hooks and captions.")

    # Extract 16 kHz mono WAV
    if progress_callback:
        progress_callback("Extracting 16 kHz audio...", 15)
    extract_audio(video_path, audio_path, sample_rate=Config.AUDIO_SAMPLE_RATE)
    
    metadata["job_id"] = job_dir.name
    metadata["source"] = source
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    
    return {
        "job_id": job_dir.name,
        "job_dir": str(job_dir),
        "video_path": str(video_path),
        "audio_path": str(audio_path),
        "metadata_path": str(metadata_path),
        "metadata": metadata,
        "cached": False,
    }
