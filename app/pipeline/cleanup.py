import os
import time
import shutil
import logging
import threading
from pathlib import Path
from typing import Dict, Any
from app.config import Config

logger = logging.getLogger("clipag.cleanup")

def clean_expired_jobs(max_age_hours: int = 24) -> Dict[str, Any]:
    """
    Scans work/ and outputs/ directories and deletes job folders older than max_age_hours.
    Also purges temporary upload files.
    """
    now = time.time()
    max_age_sec = max_age_hours * 3600
    cleaned_work = 0
    cleaned_outputs = 0
    freed_bytes = 0

    # 1. Clean work directory
    if Config.WORK_DIR.exists():
        for item in Config.WORK_DIR.iterdir():
            if item.is_dir() and item.name != "uploads":
                try:
                    mtime = item.stat().st_mtime
                    if now - mtime > max_age_sec:
                        dir_size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                        shutil.rmtree(item, ignore_errors=True)
                        cleaned_work += 1
                        freed_bytes += dir_size
                        logger.info(f"Cleaned expired work dir: {item.name} (age: {(now - mtime)/3600:.1f}h)")
                except Exception as e:
                    logger.warning(f"Error cleaning work dir {item.name}: {e}")

    # 2. Clean uploads directory
    uploads_dir = Config.WORK_DIR / "uploads"
    if uploads_dir.exists():
        for item in uploads_dir.iterdir():
            if item.is_file():
                try:
                    mtime = item.stat().st_mtime
                    if now - mtime > max_age_sec:
                        freed_bytes += item.stat().st_size
                        item.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"Error cleaning upload {item.name}: {e}")

    # 3. Clean outputs directory
    if Config.OUTPUTS_DIR.exists():
        for item in Config.OUTPUTS_DIR.iterdir():
            if item.is_dir():
                try:
                    mtime = item.stat().st_mtime
                    if now - mtime > max_age_sec:
                        dir_size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                        shutil.rmtree(item, ignore_errors=True)
                        cleaned_outputs += 1
                        freed_bytes += dir_size
                        logger.info(f"Cleaned expired output dir: {item.name} (age: {(now - mtime)/3600:.1f}h)")
                except Exception as e:
                    logger.warning(f"Error cleaning output dir {item.name}: {e}")

    mb_freed = round(freed_bytes / (1024 * 1024), 2)
    logger.info(f"Cleanup complete: removed {cleaned_work} work dirs, {cleaned_outputs} output dirs. Freed {mb_freed} MB.")
    return {
        "cleaned_work": cleaned_work,
        "cleaned_outputs": cleaned_outputs,
        "freed_mb": mb_freed,
    }

def clean_transient_job_files(job_dir: Path):
    """
    Purges temporary intermediate frames or large raw caches after a job finishes,
    preserving essential video/audio/transcript files.
    """
    try:
        for temp_pattern in ["sampled_frames", "temp_*", "*.tmp"]:
            for match in job_dir.glob(temp_pattern):
                if match.is_dir():
                    shutil.rmtree(match, ignore_errors=True)
                elif match.is_file():
                    match.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Error cleaning transient files in {job_dir}: {e}")

def _cleanup_worker_loop(interval_hours: int, retention_hours: int):
    while True:
        try:
            time.sleep(interval_hours * 3600)
            clean_expired_jobs(max_age_hours=retention_hours)
        except Exception as e:
            logger.error(f"Error in cleanup background worker: {e}")

def start_cleanup_scheduler(interval_hours: int = 1, retention_hours: int = 24):
    """Starts background daemon thread that cleans old jobs hourly."""
    # Run once at startup
    try:
        clean_expired_jobs(max_age_hours=retention_hours)
    except Exception as e:
        logger.warning(f"Startup cleanup error: {e}")

    t = threading.Thread(
        target=_cleanup_worker_loop,
        args=(interval_hours, retention_hours),
        daemon=True,
    )
    t.start()
    logger.info(f"Started auto-cleanup daemon (interval: {interval_hours}h, retention: {retention_hours}h)")
