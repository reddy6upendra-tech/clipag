import json
import wave
import shutil
import logging
import subprocess
import concurrent.futures
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
    """Loads and caches WhisperModel in memory for subsequent requests with multi-threading and worker streams."""
    from faster_whisper import WhisperModel
    import os
    name = model_name or Config.WHISPER_MODEL
    num_threads = min(8, max(4, os.cpu_count() or 4))
    num_workers = max(1, Config.WHISPER_WORKERS)
    key = f"{name}_{Config.WHISPER_DEVICE}_{Config.WHISPER_COMPUTE_TYPE}_{num_threads}_{num_workers}"
    
    if key not in _WHISPER_MODEL_CACHE:
        logger.info(
            f"Loading faster-whisper model '{name}' (device={Config.WHISPER_DEVICE}, "
            f"compute={Config.WHISPER_COMPUTE_TYPE}, threads={num_threads}, workers={num_workers})..."
        )
        try:
            _WHISPER_MODEL_CACHE[key] = WhisperModel(
                name,
                device=Config.WHISPER_DEVICE,
                compute_type=Config.WHISPER_COMPUTE_TYPE,
                cpu_threads=num_threads,
                num_workers=num_workers,
            )
        except Exception as e:
            err_str = str(e)
            if Config.WHISPER_DEVICE == "cuda":
                logger.warning(
                    f"Failed to load Whisper on CUDA ({err_str}). "
                    "Auto-fallback to CPU (int8) to ensure processing succeeds."
                )
                Config.WHISPER_DEVICE = "cpu"
                Config.WHISPER_COMPUTE_TYPE = "int8"
                key_cpu = f"{name}_cpu_int8_{num_threads}_{num_workers}"
                _WHISPER_MODEL_CACHE[key_cpu] = WhisperModel(
                    name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=num_threads,
                    num_workers=num_workers,
                )
                return _WHISPER_MODEL_CACHE[key_cpu]
            raise
    return _WHISPER_MODEL_CACHE[key]

def format_timestamp(seconds: float) -> str:
    """Formats seconds into MM:SS or H:MM:SS format for long podcasts."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def slice_audio_into_chunks(
    audio_path: Path,
    chunks_dir: Path,
    chunk_seconds: float = 300.0,
    silence_gaps: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Slices a long audio WAV file into ~5-minute chunks, intelligently aligning cuts
    to silence pauses (from audio_energy) so no words are severed mid-sentence.
    """
    chunks_dir.mkdir(parents=True, exist_ok=True)
    
    with wave.open(str(audio_path), "rb") as wf:
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        total_sec = n_frames / float(framerate) if framerate > 0 else 0.0

    # If short audio (<= 6 minutes), process in one pass without chunking
    if total_sec <= 360.0:
        return [{
            "index": 0,
            "path": audio_path,
            "start": 0.0,
            "end": total_sec,
            "duration": total_sec,
            "is_single": True,
        }]

    gaps = silence_gaps or []
    cut_points = [0.0]
    cur = chunk_seconds

    while cur < total_sec - 60.0:
        # Search for silence gap in window [cur - 25s, cur + 25s]
        candidate_gaps = [
            g for g in gaps
            if (cur - 25.0) <= g["start"] <= (cur + 25.0) and g.get("duration", 0) >= 0.4
        ]
        if candidate_gaps:
            best_gap = min(candidate_gaps, key=lambda g: abs(g["start"] - cur))
            cut = round(best_gap["start"] + (best_gap["duration"] / 2.0), 3)
        else:
            cut = round(cur, 3)

        if cut > cut_points[-1] + 60.0:
            cut_points.append(cut)
        cur += chunk_seconds

    cut_points.append(total_sec)

    chunks = []
    for idx in range(len(cut_points) - 1):
        c_start = cut_points[idx]
        c_end = cut_points[idx + 1]
        c_dur = round(c_end - c_start, 3)
        c_path = chunks_dir / f"chunk_{idx:03d}.wav"

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{c_start:.3f}",
            "-t", f"{c_dur:.3f}",
            "-i", str(audio_path),
            "-c", "copy",
            str(c_path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0 or not c_path.exists():
            # Fallback to re-encoding PCM if stream copy fails
            cmd_re = [
                "ffmpeg", "-y",
                "-ss", f"{c_start:.3f}",
                "-t", f"{c_dur:.3f}",
                "-i", str(audio_path),
                "-acodec", "pcm_s16le",
                str(c_path),
            ]
            subprocess.run(cmd_re, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        chunks.append({
            "index": idx,
            "path": c_path,
            "start": c_start,
            "end": c_end,
            "duration": c_dur,
            "is_single": False,
        })

    logger.info(f"Split {total_sec:.1f}s audio into {len(chunks)} silence-aligned chunks (target: {chunk_seconds}s each).")
    return chunks

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
    Supports GPU acceleration (CUDA) and parallel ~5-minute chunk processing for long podcasts.
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
        if req_lang == "auto" or cached_lang == req_lang:
            logger.info(f"Transcription cache hit for {audio_path.name} (language: {data.get('language')})")
            if energy_path.exists():
                with open(energy_path, "r", encoding="utf-8") as f:
                    data["audio_energy"] = json.load(f)
            data["cached"] = True
            return data

    # 2. Audio Dynamics & Silence Analysis
    if progress_callback:
        progress_callback("Analyzing audio dynamics and silence pauses...", 20)
    audio_energy = analyze_audio_energy(audio_path)
    with open(energy_path, "w", encoding="utf-8") as f:
        json.dump(audio_energy, f, indent=2)

    silence_gaps = audio_energy.get("silence_gaps", [])

    # 3. Model setup
    if progress_callback:
        progress_callback(f"Loading faster-whisper on {Config.WHISPER_DEVICE.upper()} ({Config.WHISPER_COMPUTE_TYPE})...", 24)

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

    # 4. Check for chunking (5-minute chunks for long audio)
    chunks_dir = job_dir / "audio_chunks"
    chunks = slice_audio_into_chunks(
        audio_path=audio_path,
        chunks_dir=chunks_dir,
        chunk_seconds=Config.WHISPER_CHUNK_SECONDS,
        silence_gaps=silence_gaps,
    )

    all_words = []
    segments_list = []
    text_lines = []

    if len(chunks) == 1 and chunks[0].get("is_single"):
        # Single-pass transcription for short audio
        if progress_callback:
            progress_callback(f"Transcribing audio with faster-whisper ({Config.WHISPER_DEVICE})...", 26)

        segments_gen, info = model.transcribe(str(audio_path), **transcribe_kwargs)
        detected_lang = req_lang if req_lang not in ("auto", "none", "") else info.language
        lang_prob = info.language_probability
        total_dur = info.duration

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

            m_s, s_s = int(seg.start // 60), int(seg.start % 60)
            m_e, s_e = int(seg.end // 60), int(seg.end % 60)
            text_lines.append(f"[{m_s:02d}:{s_s:02d}-{m_e:02d}:{s_e:02d}] {seg.text.strip()}")
            
            dur_pct = min(1.0, seg.end / total_dur) if total_dur > 0 else 0.5
            if progress_callback and seg_idx % 4 == 0:
                step_pct = 26 + int(dur_pct * 14)
                progress_callback(
                    f"Transcribing: {format_timestamp(seg.end)} / {format_timestamp(total_dur)} ({int(dur_pct * 100)}%)...",
                    step_pct,
                )
    else:
        # Multi-chunk parallel transcription!
        logger.info(
            f"Transcribing {len(chunks)} parallel 5-min chunks using "
            f"{Config.WHISPER_WORKERS} workers on {Config.WHISPER_DEVICE}..."
        )

        def transcribe_single_chunk(chunk_info: Dict[str, Any]) -> Dict[str, Any]:
            seg_gen, info = model.transcribe(str(chunk_info["path"]), **transcribe_kwargs)
            offset = chunk_info["start"]
            c_words = []
            c_segments = []
            for seg in seg_gen:
                seg_words = []
                if seg.words:
                    for w in seg.words:
                        wd = {
                            "word": w.word.strip(),
                            "start": round(w.start + offset, 3),
                            "end": round(w.end + offset, 3),
                            "probability": round(w.probability, 3),
                        }
                        seg_words.append(wd)
                        c_words.append(wd)

                c_segments.append({
                    "start": round(seg.start + offset, 3),
                    "end": round(seg.end + offset, 3),
                    "text": seg.text.strip(),
                    "words": seg_words,
                    "language": getattr(seg, "language", info.language),
                })

            return {
                "index": chunk_info["index"],
                "words": c_words,
                "segments": c_segments,
                "detected_lang": info.language,
                "lang_prob": info.language_probability,
                "duration": chunk_info["duration"],
            }

        completed_count = 0
        chunk_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=Config.WHISPER_WORKERS) as executor:
            futures = {executor.submit(transcribe_single_chunk, chunk): chunk for chunk in chunks}
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                chunk_results.append(res)
                completed_count += 1
                pct = 25 + int((completed_count / len(chunks)) * 15)
                if progress_callback:
                    progress_callback(
                        f"Transcribing parallel chunks: {completed_count}/{len(chunks)} completed ({int(completed_count/len(chunks)*100)}%)...",
                        pct,
                    )
                logger.info(f"Chunk {res['index']+1}/{len(chunks)} finished ({len(res['words'])} words)")

        # Re-sort chunks chronologically
        chunk_results.sort(key=lambda x: x["index"])

        global_seg_idx = 0
        for cr in chunk_results:
            all_words.extend(cr["words"])
            for s in cr["segments"]:
                s["id"] = global_seg_idx
                segments_list.append(s)
                m_s, s_s = int(s["start"] // 60), int(s["start"] % 60)
                m_e, s_e = int(s["end"] // 60), int(s["end"] % 60)
                text_lines.append(f"[{m_s:02d}:{s_s:02d}-{m_e:02d}:{s_e:02d}] {s['text']}")
                global_seg_idx += 1

        detected_lang = req_lang if req_lang not in ("auto", "none", "") else (chunk_results[0]["detected_lang"] if chunk_results else "en")
        lang_prob = chunk_results[0]["lang_prob"] if chunk_results else 1.0
        total_dur = sum(c["duration"] for c in chunks)

        # Cleanup temporary chunks directory
        shutil.rmtree(chunks_dir, ignore_errors=True)

    if not all_words:
        raise RuntimeError(
            "No speech detected in this audio. The video may contain only background music, ambient sound, or silence. "
            "AI Reels Clipper requires spoken dialogue to extract viral hooks and captions."
        )

    transcript_data = {
        "language": detected_lang,
        "requested_language": req_lang,
        "language_probability": round(lang_prob, 3),
        "duration": round(total_dur, 2),
        "segments": segments_list,
        "words": all_words,
        "audio_energy": audio_energy,
        "parallel_chunks_used": len(chunks) if len(chunks) > 1 else 1,
        "cached": False,
    }

    # Save transcript.json
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(transcript_data, f, indent=2, ensure_ascii=False)

    # Save transcript.txt
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(text_lines))

    logger.info(f"Transcription finished: {len(segments_list)} segments, {len(all_words)} words across {len(chunks)} chunk(s).")
    return transcript_data
