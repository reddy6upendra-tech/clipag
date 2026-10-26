import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import Config
from app.pipeline.audio_analysis import analyze_audio_energy

# Compatibility patch: PyAV 19+ removed 'metadata_errors' parameter
try:
    import av
    _orig_av_open = av.open
    def _safe_av_open(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        return _orig_av_open(*args, **kwargs)
    av.open = _safe_av_open
except Exception:
    pass

logger = logging.getLogger("clipag.transcribe")

_WHISPER_MODEL_CACHE: Dict[str, Any] = {}

def get_whisper_model(model_name: Optional[str] = None):
    """Loads and caches WhisperModel in memory for subsequent requests with multi-threading."""
    from faster_whisper import WhisperModel
    import os
    name = model_name or Config.WHISPER_MODEL
    num_threads = min(8, max(4, os.cpu_count() or 4))
    key = f"{name}_{Config.WHISPER_DEVICE}_{Config.WHISPER_COMPUTE_TYPE}_{num_threads}"
    
    if key not in _WHISPER_MODEL_CACHE:
        logger.info(f"Loading faster-whisper model '{name}' (device={Config.WHISPER_DEVICE}, compute={Config.WHISPER_COMPUTE_TYPE}, threads={num_threads})...")
        _WHISPER_MODEL_CACHE[key] = WhisperModel(
            name,
            device=Config.WHISPER_DEVICE,
            compute_type=Config.WHISPER_COMPUTE_TYPE,
            cpu_threads=num_threads,
            num_workers=1,
        )
    return _WHISPER_MODEL_CACHE[key]

def format_timestamp(seconds: float) -> str:
    """Formats seconds into MM:SS or H:MM:SS format for long podcasts."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def transcribe_audio(
    audio_path: Path,
    job_dir: Path,
    model_name: Optional[str] = None,
    language: Optional[str] = "auto",
    force_recompute: bool = False,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Transcribes a 16 kHz mono WAV file using faster-whisper.
    Produces transcript.json and transcript.txt with word-level timestamps.
    Caches results by checking if transcript.json already exists.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    json_path = job_dir / "transcript.json"
    txt_path = job_dir / "transcript.txt"
    energy_path = job_dir / "audio_energy.json"
    req_lang = (language or "auto").lower()

    # 1. Check cache
    if not force_recompute and json_path.exists() and txt_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cached_lang = data.get("requested_language", "auto")
        # If user explicitly requested a specific language and cache had auto or different, recompute
        if req_lang == "auto" or cached_lang == req_lang:
            logger.info(f"Transcription cache hit for {audio_path.name} (language: {data.get('language')})")
            if energy_path.exists():
                with open(energy_path, "r", encoding="utf-8") as f:
                    data["audio_energy"] = json.load(f)
            data["cached"] = True
            return data

    if progress_callback:
        progress_callback("Analyzing audio dynamics...", 20)
    audio_energy = analyze_audio_energy(audio_path)
    with open(energy_path, "w", encoding="utf-8") as f:
        json.dump(audio_energy, f, indent=2)

    if progress_callback:
        progress_callback("Transcribing speech with faster-whisper (CPU int8)...", 25)

    model = get_whisper_model(model_name)
    
    transcribe_kwargs = {
        "vad_filter": Config.WHISPER_VAD_FILTER,
        "vad_parameters": dict(min_silence_duration_ms=500),
        "word_timestamps": True,
        "beam_size": 2 if (req_lang not in ("auto", "none", "", "en")) else 1,
        "best_of": 1,
        "condition_on_previous_text": False,
    }
    if req_lang not in ("auto", "none", ""):
        transcribe_kwargs["language"] = req_lang
        logger.info(f"Explicitly forcing transcription language to '{req_lang}'")

    segments_gen, info = model.transcribe(
        str(audio_path),
        **transcribe_kwargs,
    )

    detected_lang = req_lang if req_lang not in ("auto", "none", "") else info.language
    logger.info(f"Active speech language: {detected_lang} (detected: {info.language}, prob: {info.language_probability:.2f})")
    logger.info(f"Detected speech language: {detected_lang} (probability: {info.language_probability:.2f})")

    segments_list = []
    all_words = []
    text_lines = []

    for seg_idx, seg in enumerate(segments_gen):
        seg_words = []
        if seg.words:
            for w in seg.words:
                word_dict = {
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3),
                }
                seg_words.append(word_dict)
                all_words.append(word_dict)

        seg_dict = {
            "id": seg_idx,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": seg_words,
            "language": getattr(seg, "language", detected_lang),
        }
        segments_list.append(seg_dict)
        
        # Build line for transcript.txt
        t_start = format_timestamp(seg.start)
        t_end = format_timestamp(seg.end)
        text_lines.append(f"[{t_start}-{t_end}] {seg.text.strip()}")

        total_dur = float(getattr(info, "duration", 0.0)) or 1.0
        dur_pct = min(1.0, seg.end / total_dur)
        if seg_idx % 5 == 0:
            logger.info(f"Transcribing audio: {format_timestamp(seg.end)} / {format_timestamp(total_dur)} ({int(dur_pct * 100)}%)")
        if progress_callback and seg_idx % 4 == 0:
            step_pct = 25 + int(dur_pct * 15)
            progress_callback(
                f"Transcribing audio: {format_timestamp(seg.end)} / {format_timestamp(total_dur)} ({int(dur_pct * 100)}%)...",
                step_pct,
            )

    if not all_words:
        raise RuntimeError(
            "No speech detected in this audio. The video may contain only background music, ambient sound, or silence. "
            "AI Reels Clipper requires spoken dialogue to extract viral hooks and captions."
        )

    transcript_data = {
        "language": detected_lang,
        "requested_language": req_lang,
        "language_probability": round(info.language_probability, 3),
        "duration": round(info.duration, 2),
        "segments": segments_list,
        "words": all_words,
        "audio_energy": audio_energy,
        "cached": False,
    }

    # Save transcript.json
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(transcript_data, f, indent=2, ensure_ascii=False)

    # Save transcript.txt
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(text_lines))

    logger.info(f"Transcription finished: {len(segments_list)} segments, {len(all_words)} words.")
    return transcript_data
