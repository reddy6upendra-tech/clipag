import os
import time
import json
import shutil
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import Config
from app.llm.client import ClipCandidate
from app.pipeline.renderer import render_single_clip, package_reels
from app.pipeline.queue_manager import GLOBAL_QUEUE
from app.pipeline.cleanup import start_cleanup_scheduler, clean_expired_jobs
from app.doctor import check_ffmpeg, check_gemini_key

logger = logging.getLogger("clipag.api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start cleanup daemon
    start_cleanup_scheduler(interval_hours=1, retention_hours=24)
    yield
    # Shutdown

app = FastAPI(title="AI Reels Clipper API", version="0.2.0", lifespan=lifespan)

# Mount static UI directory
STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount outputs directory for video streaming
app.mount("/outputs", StaticFiles(directory=str(Config.OUTPUTS_DIR)), name="outputs")

# Backward compatibility reference for tests
JOBS = GLOBAL_QUEUE._jobs

class AdjustRequest(BaseModel):
    clip_index: int
    start_delta: float = 0.0
    end_delta: float = 0.0
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    caption_style: Optional[str] = None
    caption_position: Optional[str] = "bottom"
    font_size: Optional[int] = None

class NotifyRequest(BaseModel):
    contact: str

class SwapRequest(BaseModel):
    clip_index: int
    caption_style: Optional[str] = "bold_yellow"

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found.")
    return index_file.read_text(encoding="utf-8")

@app.get("/api/config")
async def get_app_config():
    supported_languages = [
        {"code": "auto", "name": "Auto-detect (Recommended for English/Mixed)"},
        {"code": "te", "name": "Telugu (తెలుగు)"},
        {"code": "hi", "name": "Hindi (हिन्दी)"},
        {"code": "en", "name": "English"},
        {"code": "ta", "name": "Tamil (தமிழ்)"},
        {"code": "kn", "name": "Kannada (ಕನ್ನಡ)"},
        {"code": "ml", "name": "Malayalam (മലയാളം)"},
        {"code": "es", "name": "Spanish"},
    ]
    return {
        "caption_styles": Config.CAPTION_STYLES,
        "default_model": Config.GEMINI_MODEL,
        "fallback_models": Config.GEMINI_FALLBACK_MODELS,
        "default_whisper": Config.WHISPER_MODEL,
        "has_gemini_key": bool(Config.GEMINI_API_KEY and Config.GEMINI_API_KEY != "your_gemini_api_key_here"),
        "supported_languages": supported_languages,
    }

@app.get("/api/doctor")
async def run_doctor_api():
    return {
        "ffmpeg": check_ffmpeg(),
        "gemini": check_gemini_key(),
    }

@app.post("/api/jobs")
async def create_job(
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    clips: int = Form(6),
    style: str = Form("bold_yellow"),
    model: str = Form("small"),
    language: str = Form("auto"),
    gemini_model: Optional[str] = Form(None),
    position: str = Form("bottom"),
    font_size: Optional[int] = Form(None),
    notify_contact: Optional[str] = Form(None),
):
    if not url and not file:
        raise HTTPException(status_code=400, detail="Either a video URL or a file upload must be provided.")

    job_temp_id = uuid.uuid4().hex[:12]
    uploads_dir = Config.WORK_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    if file and file.filename:
        # Save uploaded file
        saved_file_path = uploads_dir / f"{job_temp_id}_{file.filename}"
        with open(saved_file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        source_input = str(saved_file_path)
    else:
        source_input = url.strip()

    job_id = GLOBAL_QUEUE.submit_job(
        source=source_input,
        target_clips=max(5, clips),
        caption_style=style,
        whisper_model=model,
        gemini_model=gemini_model,
        caption_position=position,
        font_size=font_size,
        language=language,
        notify_contact=notify_contact,
    )

    job_info = GLOBAL_QUEUE.get_job_status(job_id)
    return {
        "job_id": job_id,
        "status": job_info["status"] if job_info else "queued",
        "queue_position": job_info.get("queue_position", 0) if job_info else 0,
    }

@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = GLOBAL_QUEUE.get_job_status(job_id)
    if not job:
        # Also check if it's already an existing content hash folder in outputs
        out_dir = Config.OUTPUTS_DIR / job_id
        if out_dir.exists() and (out_dir / "all_reels_package.zip").exists():
            clips = []
            for p in sorted((out_dir / "metadata").glob("clip_*.json")):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
                    c["video_url"] = f"/outputs/{job_id}/{c['filename']}"
                    clips.append(c)
            return {
                "id": job_id,
                "status": "completed",
                "progress": 100,
                "step": "Reels loaded from archive",
                "active_stage": "complete",
                "result": {
                    "content_hash_id": job_id,
                    "clips": clips,
                    "zip_url": f"/api/jobs/{job_id}/download-zip",
                    "total_clips": len(clips),
                }
            }
        raise HTTPException(status_code=404, detail="Job not found.")
    return job

@app.post("/api/jobs/{job_id}/notify")
async def register_job_notification(job_id: str, req: NotifyRequest):
    success = GLOBAL_QUEUE.register_notification(job_id, req.contact)
    if not success:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"status": "ok", "message": f"Notification registered for {req.contact}"}

@app.post("/api/jobs/{job_id}/swap")
async def swap_clip_endpoint(job_id: str, req: SwapRequest):
    """Swaps an existing clip with a freshly discovered alternative viral moment from the transcript."""
    try:
        updated_meta = GLOBAL_QUEUE.swap_clip(
            content_hash_id=job_id,
            clip_index=req.clip_index,
            caption_style=req.caption_style or "bold_yellow",
        )
        return updated_meta
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=404, detail=str(fnf))
    except Exception as e:
        logger.exception(f"Error swapping clip {req.clip_index} for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/jobs/{job_id}/adjust")
async def adjust_clip(job_id: str, req: AdjustRequest):
    """
    Adjusts the start and/or end of a specific clip and re-renders ONLY that clip.
    Supports either relative deltas or absolute start_time / end_time.
    """
    out_dir = Config.OUTPUTS_DIR / job_id
    if not out_dir.exists():
        raise HTTPException(status_code=404, detail=f"Job directory {job_id} not found.")

    meta_path = out_dir / "metadata" / f"clip_{req.clip_index:02d}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Clip {req.clip_index} metadata not found.")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # Calculate new boundaries
    if req.start_time is not None:
        new_start = max(0.0, req.start_time)
    else:
        new_start = max(0.0, meta["start_sec"] + req.start_delta)

    if req.end_time is not None:
        new_end = max(new_start + 5.0, req.end_time)
    else:
        new_end = max(new_start + 5.0, meta["end_sec"] + req.end_delta)

    # Load work job directory for transcript words
    work_dir = Config.WORK_DIR / job_id
    transcript_json_path = work_dir / "transcript.json"
    words = []
    lang = None
    if transcript_json_path.exists():
        with open(transcript_json_path, "r", encoding="utf-8") as f:
            t_data = json.load(f)
            words = t_data.get("words", [])
            lang = t_data.get("language")

    video_path = work_dir / "source.mp4"
    if not video_path.exists():
        matches = list(work_dir.glob("source.*"))
        if matches:
            video_path = matches[0]

    clip_candidate = ClipCandidate(
        start=new_start,
        end=new_end,
        title=meta.get("title", f"Clip #{req.clip_index}"),
        hook_line=meta.get("hook_line", ""),
        reason=meta.get("reason", ""),
        score=meta.get("score", 85),
        suggested_hashtags=meta.get("hashtags", ["#reels"]),
        description=meta.get("description", ""),
    )

    caption_style = req.caption_style or "bold_yellow"
    caption_position = req.caption_position or "bottom"
    updated_meta = render_single_clip(
        video_path=video_path,
        clip=clip_candidate,
        clip_idx=req.clip_index,
        output_dir=out_dir,
        words=words,
        language=lang,
        caption_style=caption_style,
        caption_position=caption_position,
        font_size_override=req.font_size,
        force_recompute=True,
    )

    # Re-package ZIP
    all_metas = []
    for p in sorted((out_dir / "metadata").glob("clip_*.json")):
        with open(p, "r", encoding="utf-8") as f:
            all_metas.append(json.load(f))
    package_reels(out_dir, all_metas)

    updated_meta["video_url"] = f"/outputs/{job_id}/{updated_meta['filename']}?t={int(time.time())}"
    return updated_meta

@app.get("/api/jobs/{content_hash_id}/download-zip")
async def download_reels_zip(content_hash_id: str):
    zip_path = Config.OUTPUTS_DIR / content_hash_id / "all_reels_package.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="ZIP package not found.")
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"reels_{content_hash_id}.zip",
    )

@app.post("/api/cleanup")
async def trigger_cleanup():
    res = clean_expired_jobs(max_age_hours=24)
    return res
