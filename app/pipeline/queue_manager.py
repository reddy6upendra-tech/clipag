import time
import json
import uuid
import queue
import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List
from app.config import Config
from app.pipeline.ingest import ingest_source
from app.pipeline.transcribe import transcribe_audio
from app.llm.gemini import GeminiProvider
from app.llm.client import ClipCandidate
from app.pipeline.renderer import render_single_clip, package_reels
from app.pipeline.cleanup import clean_transient_job_files

logger = logging.getLogger("clipag.queue")

class JobQueueManager:
    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._active_job_id: Optional[str] = None
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        logger.info("FIFO Job Queue initialized and worker thread running.")

    def submit_job(
        self,
        source: str,
        target_clips: int,
        caption_style: str,
        whisper_model: str,
        gemini_model: Optional[str],
        caption_position: str = "bottom",
        font_size: Optional[int] = None,
        language: str = "auto",
        task: str = "translate",
        notify_contact: Optional[str] = None,
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            # Determine if it will run immediately or be queued
            is_queued = (self._active_job_id is not None) or (not self._queue.empty())
            status = "queued" if is_queued else "initializing"
            queue_pos = self._queue.qsize() + (1 if self._active_job_id else 0)

            now_str = time.strftime("%H:%M:%S")
            initial_msg = f"Job {job_id} registered. Target: {target_clips or 'Auto (Dynamic)'} reels | Style: {caption_style} | Whisper: {whisper_model} | Lang: {language} ({task})"
            self._jobs[job_id] = {
                "id": job_id,
                "status": status,
                "queue_position": queue_pos if is_queued else 0,
                "progress": 0,
                "step": f"Waiting in queue (Position #{queue_pos})..." if is_queued else "Initializing job...",
                "active_stage": "queue" if is_queued else "ingest",
                "error": None,
                "error_stage": None,
                "result": None,
                "logs": [{"time": now_str, "stage": "queue" if is_queued else "init", "message": initial_msg}],
                "notify_contact": notify_contact,
                "created_at": time.time(),
                "completed_at": None,
                "params": {
                    "source": source,
                    "target_clips": target_clips,
                    "caption_style": caption_style,
                    "whisper_model": whisper_model,
                    "gemini_model": gemini_model,
                    "caption_position": caption_position,
                    "font_size": font_size,
                    "language": language,
                    "task": task,
                }
            }

        self._queue.put(job_id)
        logger.info(f"Enqueued job {job_id} (status: {status}, queue_position: {queue_pos})")
        return job_id

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            
            # Recalculate queue position dynamically if queued
            if job["status"] == "queued":
                # Find position in queue
                # Approximate based on queue size
                active_str = " (Another video is currently processing)" if self._active_job_id else ""
                job["step"] = f"Waiting in queue (Position #{job.get('queue_position', 1)}){active_str}..."
            
            return dict(job)

    def register_notification(self, job_id: str, contact: str) -> bool:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["notify_contact"] = contact.strip()
                logger.info(f"Registered notification contact for job {job_id}: {contact}")
                return True
        return False

    def update_job(
        self,
        job_id: str,
        step: str,
        progress: Optional[int] = None,
        active_stage: Optional[str] = None,
        error: Optional[str] = None,
        error_stage: Optional[str] = None,
    ):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["step"] = step
                if progress is not None:
                    self._jobs[job_id]["progress"] = progress
                if active_stage:
                    self._jobs[job_id]["active_stage"] = active_stage

                # Live terminal logs stream
                now_str = time.strftime("%H:%M:%S")
                stage_name = active_stage or self._jobs[job_id].get("active_stage", "info")
                logs = self._jobs[job_id].setdefault("logs", [])

                # Update in-place for active streaming indicators (download % or transcription chunks)
                is_stream_tick = (
                    logs and (
                        ("[yt-dlp] Downloading:" in step and "[yt-dlp] Downloading:" in logs[-1]["message"]) or
                        ("Processing speech:" in step and "Processing speech:" in logs[-1]["message"]) or
                        ("Transcribing parallel chunks:" in step and "Transcribing parallel chunks:" in logs[-1]["message"])
                    )
                )

                if is_stream_tick:
                    logs[-1]["time"] = now_str
                    logs[-1]["message"] = step
                    logs[-1]["stage"] = stage_name
                else:
                    logs.append({
                        "time": now_str,
                        "stage": stage_name,
                        "message": step,
                    })
                    if len(logs) > 600:
                        logs.pop(0)

                if error is not None:
                    self._jobs[job_id]["status"] = "failed"
                    self._jobs[job_id]["error"] = error
                    self._jobs[job_id]["error_stage"] = error_stage or active_stage or "pipeline"
                    logs.append({
                        "time": now_str,
                        "stage": "error",
                        "message": f"❌ ERROR [{error_stage or active_stage}]: {error}",
                    })
                logger.info(f"Job {job_id} [{active_stage}]: {step} ({progress}%)")

    def _worker_loop(self):
        while True:
            job_id = self._queue.get()
            with self._lock:
                self._active_job_id = job_id
                if job_id in self._jobs:
                    self._jobs[job_id]["status"] = "running"
                    self._jobs[job_id]["queue_position"] = 0
                    self._jobs[job_id]["step"] = "Starting pipeline..."
                    self._jobs[job_id]["active_stage"] = "ingest"

            try:
                self._execute_pipeline(job_id)
            except Exception as e:
                logger.exception(f"Unhandled error in worker execution of job {job_id}: {e}")
                self.update_job(job_id, f"Error: {str(e)}", error=str(e), error_stage="general")
            finally:
                with self._lock:
                    self._active_job_id = None
                    # Update remaining queued jobs' positions
                    current_items = list(self._queue.queue)
                    for pos, q_job_id in enumerate(current_items, start=1):
                        if q_job_id in self._jobs:
                            self._jobs[q_job_id]["queue_position"] = pos
                self._queue.task_done()

    def _execute_pipeline(self, job_id: str):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            params = job["params"]

        source = params["source"]
        target_clips = params["target_clips"]
        caption_style = params["caption_style"]
        whisper_model = params["whisper_model"]
        gemini_model = params["gemini_model"]
        caption_position = params["caption_position"]
        font_size = params["font_size"]
        language = params.get("language", "auto")
        task = params.get("task", "translate")

        def progress_cb(msg: str, pct: Optional[int]):
            self.update_job(job_id, msg, pct)

        try:
            # 1. Ingest
            self.update_job(job_id, "Verifying and downloading video...", 5, active_stage="ingest")
            ingest_res = ingest_source(source, progress_callback=progress_cb)
            content_hash_id = ingest_res["job_id"]
            job_dir = Path(ingest_res["job_dir"])
            video_path = Path(ingest_res["video_path"])
            audio_path = Path(ingest_res["audio_path"])
            total_duration = ingest_res["metadata"]["duration"]

            # 2. Transcription
            lang_label = f"{language} -> English captions" if task == "translate" and language != "en" else language
            self.update_job(job_id, f"Transcribing audio with faster-whisper ({whisper_model}, {lang_label})...", 20, active_stage="transcribe")
            transcript_data = transcribe_audio(
                audio_path=audio_path,
                job_dir=job_dir,
                model_name=whisper_model,
                language=language,
                task=task,
                progress_callback=progress_cb,
            )

            # 3. Clip Finding
            self.update_job(job_id, "Finding high-retention viral moments...", 40, active_stage="select")
            provider = GeminiProvider()
            if gemini_model:
                provider.primary_model = gemini_model

            clips = provider.find_clips(
                transcript_data=transcript_data,
                audio_hints=transcript_data.get("audio_energy"),
                target_clips=target_clips,
                min_duration=Config.MIN_CLIP_DURATION,
                max_duration=Config.MAX_CLIP_DURATION,
                total_video_duration=total_duration,
                progress_callback=progress_cb,
            )

            # 4. Rendering
            output_dir = Config.OUTPUTS_DIR / content_hash_id
            output_dir.mkdir(parents=True, exist_ok=True)
            rendered_clips = []
            total_clips = len(clips)

            for idx, clip in enumerate(clips, start=1):
                base_pct = 50 + int((idx - 1) / total_clips * 45)
                self.update_job(
                    job_id,
                    f"Reframing & Rendering Reel {idx}/{total_clips}: '{clip.title[:25]}'...",
                    base_pct,
                    active_stage="render",
                )
                
                meta = render_single_clip(
                    video_path=video_path,
                    clip=clip,
                    clip_idx=idx,
                    output_dir=output_dir,
                    words=transcript_data["words"],
                    language=transcript_data.get("language"),
                    caption_style=caption_style,
                    caption_position=caption_position,
                    font_size_override=font_size,
                    progress_callback=progress_cb,
                )
                meta["video_url"] = f"/outputs/{content_hash_id}/{meta['filename']}"
                meta["score"] = clip.score
                meta["hook_line"] = clip.hook_line
                meta["reason"] = clip.reason
                rendered_clips.append(meta)

            # 5. Packaging
            self.update_job(job_id, "Packaging reels and metadata...", 98, active_stage="package")
            zip_path = package_reels(output_dir, rendered_clips)
            clean_transient_job_files(job_dir)

            self.update_job(job_id, f"Pipeline complete: {len(rendered_clips)} viral vertical reels rendered and packaged in ZIP!", 100, active_stage="complete")

            with self._lock:
                self._jobs[job_id]["status"] = "completed"
                self._jobs[job_id]["progress"] = 100
                self._jobs[job_id]["step"] = "All clips completed and ready for download!"
                self._jobs[job_id]["active_stage"] = "complete"
                self._jobs[job_id]["completed_at"] = time.time()
                self._jobs[job_id]["result"] = {
                    "content_hash_id": content_hash_id,
                    "clips": rendered_clips,
                    "zip_url": f"/api/jobs/{content_hash_id}/download-zip",
                    "total_clips": len(rendered_clips),
                    "language": transcript_data.get("language"),
                }

                # Send simulated or webhook notification
                contact = self._jobs[job_id].get("notify_contact")
                if contact:
                    logger.info(f"*** NOTIFICATION SENT *** To: {contact} -> Your {len(rendered_clips)} reels for job {job_id} are ready!")

        except Exception as e:
            logger.exception(f"Pipeline error for job {job_id}: {e}")
            stage = self._jobs.get(job_id, {}).get("active_stage", "pipeline")
            self.update_job(job_id, f"Error during {stage}: {str(e)}", error=str(e), error_stage=stage)

    def swap_clip(self, content_hash_id: str, clip_index: int, caption_style: str = "bold_yellow") -> Dict[str, Any]:
        """
        Finds an alternative candidate clip from the transcript that doesn't overlap existing clips,
        re-renders it, and updates metadata/ZIP.
        """
        work_dir = Config.WORK_DIR / content_hash_id
        out_dir = Config.OUTPUTS_DIR / content_hash_id
        if not work_dir.exists() or not out_dir.exists():
            raise FileNotFoundError(f"Job content directory {content_hash_id} not found.")

        transcript_path = work_dir / "transcript.json"
        metadata_path = work_dir / "metadata.json"
        if not transcript_path.exists() or not metadata_path.exists():
            raise FileNotFoundError("Transcript or metadata not found for this job.")

        with open(transcript_path, "r", encoding="utf-8") as f:
            t_data = json.load(f)
        with open(metadata_path, "r", encoding="utf-8") as f:
            m_data = json.load(f)

        words = t_data.get("words", [])
        total_duration = m_data.get("duration", 0.0)

        # Collect existing clip boundaries to avoid overlaps
        existing_ranges = []
        for p in sorted((out_dir / "metadata").glob("clip_*.json")):
            with open(p, "r", encoding="utf-8") as f:
                c_meta = json.load(f)
                if c_meta.get("clip_index") != clip_index:
                    existing_ranges.append((c_meta["start_sec"], c_meta["end_sec"]))

        # Use heuristics to find candidates
        from app.pipeline.heuristics import HeuristicClipFinder
        finder = HeuristicClipFinder()
        candidates = finder.find_clips(
            words=words,
            audio_hints=t_data.get("audio_energy"),
            total_duration=total_duration,
            target_clips=10,
        )

        # Filter candidate that doesn't significantly overlap existing ranges
        selected = None
        for cand in candidates:
            cand_mid = (cand.start + cand.end) / 2.0
            overlap = any(cand.start < end and cand.end > start for (start, end) in existing_ranges)
            if not overlap:
                selected = cand
                break
        
        if not selected and candidates:
            selected = candidates[-1]

        if not selected:
            raise RuntimeError("Could not find a suitable alternative clip.")

        video_path = work_dir / "source.mp4"
        if not video_path.exists():
            matches = list(work_dir.glob("source.*"))
            if matches:
                video_path = matches[0]

        updated_meta = render_single_clip(
            video_path=video_path,
            clip=selected,
            clip_idx=clip_index,
            output_dir=out_dir,
            words=words,
            language=t_data.get("language"),
            caption_style=caption_style,
            force_recompute=True,
        )

        # Re-package ZIP
        all_metas = []
        for p in sorted((out_dir / "metadata").glob("clip_*.json")):
            with open(p, "r", encoding="utf-8") as f:
                all_metas.append(json.load(f))
        package_reels(out_dir, all_metas)

        updated_meta["video_url"] = f"/outputs/{content_hash_id}/{updated_meta['filename']}?t={int(time.time())}"
        return updated_meta

# Singleton instance
GLOBAL_QUEUE = JobQueueManager()
