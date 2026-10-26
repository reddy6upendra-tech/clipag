import time
import pytest
from pathlib import Path
from app.config import Config
from app.pipeline.queue_manager import JobQueueManager
from app.pipeline.cleanup import clean_expired_jobs

def test_queue_ordering():
    qm = JobQueueManager()
    job1 = qm.submit_job(
        source="test1.mp4",
        target_clips=5,
        caption_style="bold_yellow",
        whisper_model="tiny",
        gemini_model=None,
    )
    job2 = qm.submit_job(
        source="test2.mp4",
        target_clips=5,
        caption_style="bold_yellow",
        whisper_model="tiny",
        gemini_model=None,
    )

    st1 = qm.get_job_status(job1)
    st2 = qm.get_job_status(job2)
    assert st1 is not None
    assert st2 is not None
    assert st1["id"] == job1
    assert st2["id"] == job2

def test_cleanup_expired_jobs(tmp_path):
    old_dir = Config.WORK_DIR / "expired_test_dir"
    old_dir.mkdir(parents=True, exist_ok=True)
    test_file = old_dir / "temp.txt"
    test_file.write_text("sample old data", encoding="utf-8")

    # Set mtime back by 48 hours
    past_time = time.time() - (48 * 3600)
    import os
    os.utime(test_file, (past_time, past_time))
    os.utime(old_dir, (past_time, past_time))

    res = clean_expired_jobs(max_age_hours=24)
    assert not old_dir.exists()
