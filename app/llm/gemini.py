import re
import json
import time
import random
import logging
import threading
from typing import List, Dict, Any, Optional
from app.config import Config
from app.llm.client import LLMProvider, ClipCandidate, ClipSelectionResponse
from app.pipeline.heuristics import find_clips_heuristic, compute_dynamic_target_clips

logger = logging.getLogger("clipag.llm.gemini")

class GlobalRateLimiter:
    """Thread-safe rate limiter enforcing max requests per minute (RPM)."""
    def __init__(self, rpm: int = 10):
        self.rpm = max(1, rpm)
        self.min_interval = 60.0 / self.rpm
        self.lock = threading.Lock()
        self.last_time = 0.0

    def wait_for_slot(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_time
            if elapsed < self.min_interval:
                sleep_needed = self.min_interval - elapsed
                time.sleep(sleep_needed)
            self.last_time = time.time()

GLOBAL_RATE_LIMITER = GlobalRateLimiter(rpm=Config.RPM_LIMIT)

def format_compact_transcript(segments: List[Dict[str, Any]], audio_hints: Optional[Dict[str, Any]] = None) -> str:
    """Builds a compact, token-efficient transcript with MM:SS timestamps and audio energy tags."""
    lines = []
    
    # Optional audio peak markers
    peak_times = set()
    if audio_hints and "peak_moments" in audio_hints:
        for p in audio_hints["peak_moments"]:
            peak_times.add(int(p.get("time", 0)))

    for s in segments:
        start_sec = s.get("start", 0.0)
        end_sec = s.get("end", 0.0)
        m_s, s_s = int(start_sec // 60), int(start_sec % 60)
        m_e, s_e = int(end_sec // 60), int(end_sec % 60)
        time_tag = f"[{m_s:02d}:{s_s:02d}-{m_e:02d}:{s_e:02d}]"
        
        # Audio cue
        cue = " [ENERGY PEAK]" if int(start_sec) in peak_times else ""
        lines.append(f"{time_tag}{cue} {s.get('text', '').strip()}")
        
    return "\n".join(lines)

def snap_to_word_boundaries(
    clips: List[ClipCandidate],
    words: List[Dict[str, Any]],
    min_duration: int = 20,
    max_duration: int = 60,
) -> List[ClipCandidate]:
    """
    Snaps clip start and end timestamps to exact Whisper word boundaries.
    Guarantees we never cut a word in half.
    """
    if not words or not clips:
        return clips

    word_starts = [w["start"] for w in words]
    word_ends = [w["end"] for w in words]

    snapped_clips = []
    for clip in clips:
        # Snap start to closest word start only if within 3.0 seconds
        closest_start = min(word_starts, key=lambda x: abs(x - clip.start))
        start_val = closest_start if abs(closest_start - clip.start) <= 3.0 else clip.start

        # Snap end to closest word end only if within 3.0 seconds
        closest_end = min(word_ends, key=lambda x: abs(x - clip.end))
        end_val = closest_end if abs(closest_end - clip.end) <= 3.0 else clip.end

        # Enforce valid ordering and min/max duration
        if end_val <= start_val:
            end_val = start_val + min_duration

        dur = end_val - start_val
        if dur < min_duration:
            candidates_end = [e for e in word_ends if (e - start_val) >= min_duration and abs(e - (start_val + min_duration)) <= 5.0]
            if candidates_end:
                end_val = min(candidates_end)
            else:
                end_val = start_val + min_duration
        elif dur > max_duration:
            candidates_end = [e for e in word_ends if (e - start_val) <= max_duration and abs(e - (start_val + max_duration)) <= 5.0]
            if candidates_end:
                end_val = max(candidates_end)
            else:
                end_val = start_val + max_duration

        snapped = ClipCandidate(
            start=round(float(start_val), 3),
            end=round(float(end_val), 3),
            title=clip.title,
            hook_line=clip.hook_line,
            reason=clip.reason,
            score=clip.score,
            suggested_hashtags=clip.suggested_hashtags,
            description=clip.description,
        )
        snapped_clips.append(snapped)

    return snapped_clips

def deduplicate_and_rank_clips(clips: List[ClipCandidate], target_clips: int = 6) -> List[ClipCandidate]:
    """Sorts by score, removes overlapping segments (>3s overlap), and selects top N."""
    sorted_clips = sorted(clips, key=lambda x: x.score, reverse=True)
    selected: List[ClipCandidate] = []
    max_count = target_clips if target_clips > 0 else 25

    for c in sorted_clips:
        if len(selected) >= max_count:
            break
        # Check overlap
        overlap = False
        for s in selected:
            overlap_sec = max(0.0, min(c.end, s.end) - max(c.start, s.start))
            if overlap_sec > 3.0:
                overlap = True
                break
        if not overlap:
            selected.append(c)

    # Sort final selection chronologically by start timestamp
    selected.sort(key=lambda x: x.start)
    return selected

class GeminiProvider(LLMProvider):
    def __init__(self):
        self.api_key = Config.GEMINI_API_KEY
        self.primary_model = Config.GEMINI_MODEL
        self.fallback_models = Config.GEMINI_FALLBACK_MODELS
        self.temperature = Config.TEMPERATURE

    def _call_gemini_api(
        self,
        prompt: str,
        model_name: str,
        progress_callback: Optional[Any] = None,
    ) -> str:
        """Calls Gemini API with structured response, exponential backoff, and RPM limiting."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ClipSelectionResponse,
            temperature=self.temperature,
        )

        max_tries = Config.MAX_RETRIES
        base_delay = 2.0

        for attempt in range(max_tries):
            # Enforce RPM limit
            GLOBAL_RATE_LIMITER.wait_for_slot()

            try:
                logger.info(f"Calling Gemini ({model_name}), attempt {attempt + 1}/{max_tries}...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                if response and response.text:
                    return response.text
                raise ValueError("Empty response received from Gemini.")
            except Exception as e:
                err_str = str(e)
                is_rate_limit = "429" in err_str or "quota" in err_str.lower() or "ResourceExhausted" in err_str
                is_server_error = "500" in err_str or "503" in err_str or "ServiceUnavailable" in err_str

                if (is_rate_limit or is_server_error) and attempt < max_tries - 1:
                    jitter = random.uniform(0.5, 1.5)
                    wait_time = (base_delay * (2 ** attempt)) + jitter
                    msg = f"Rate limit / transient API issue, retrying in {wait_time:.1f}s (attempt {attempt+1}/{max_tries})..."
                    logger.warning(msg)
                    if progress_callback:
                        progress_callback(msg, None)
                    time.sleep(wait_time)
                else:
                    logger.error(f"Gemini API call failed permanently for model {model_name}: {e}")
                    raise

        raise RuntimeError(f"Exhausted all {max_tries} retries on model {model_name}.")

    def find_clips(
        self,
        transcript_data: Dict[str, Any],
        audio_hints: Optional[Dict[str, Any]] = None,
        target_clips: int = 0,
        min_duration: int = 20,
        max_duration: int = 60,
        total_video_duration: Optional[float] = None,
        progress_callback: Optional[Any] = None,
    ) -> List[ClipCandidate]:
        """
        Executes clip selection via Gemini.
        Supports dynamic clip counting (target_clips=0 or None) based on total video duration.
        Falls back to fallback models on failure, and heuristic algorithm if all else fails.
        """
        words = transcript_data.get("words", [])
        segments = transcript_data.get("segments", [])
        total_duration = total_video_duration or transcript_data.get("duration", 0.0)

        # Dynamic clip calculation if target_clips <= 0
        if not target_clips or target_clips <= 0:
            effective_target = compute_dynamic_target_clips(total_duration)
            is_dynamic = True
        else:
            effective_target = target_clips
            is_dynamic = False

        # If no API key configured, use heuristic immediately
        if not self.api_key or self.api_key == "your_gemini_api_key_here":
            logger.info(f"No Gemini API key configured. Utilizing local heuristic clip finder (target={effective_target}).")
            if progress_callback:
                progress_callback("Selecting viral clips via heuristic algorithm (no API key)...", 45)
            clips = find_clips_heuristic(
                transcript_data=transcript_data,
                audio_hints=audio_hints,
                target_clips=effective_target,
                min_duration=min_duration,
                max_duration=max_duration,
                total_video_duration=total_duration,
            )
            return snap_to_word_boundaries(clips, words, min_duration, max_duration)

        # Build compact transcript
        compact_transcript = format_compact_transcript(segments, audio_hints)
        
        if is_dynamic:
            clip_rule = (
                f"5. DYNAMIC CLIP COUNT: Video duration is {total_duration/60:.1f} minutes. "
                f"Identify between {min(3, effective_target)} and {effective_target} genuinely high-retention, non-overlapping moments. "
                f"Do not force filler if moments lack punch, but capture every major hook, revelation, humor, or high-energy debate."
            )
        else:
            clip_rule = f"5. Pick at least {effective_target} non-overlapping clips distributed across the video."

        system_instructions = (
            "You are an expert viral content editor for Instagram Reels, YouTube Shorts, and TikTok.\n"
            "Your task is to select the BEST viral, high-retention video clips from the provided transcript.\n\n"
            "RULES:\n"
            f"1. Target clip duration: {min_duration} to {max_duration} seconds.\n"
            "2. Each clip MUST be completely self-contained, requiring no prior context.\n"
            "3. The first 3 seconds MUST have a strong hook (intrigue, bold statement, surprise, question).\n"
            "4. Each clip must START at the beginning of a sentence and END at the end of a sentence.\n"
            f"{clip_rule}\n"
            "6. Multilingual support: If the clip is in Hindi, Telugu, or mixed languages, the title MUST be written in the clip's dominant language.\n"
            "7. Provide for each clip: start, end (seconds), title, hook_line, reason, score (0-100), 5 suggested_hashtags, and description.\n"
        )

        models_to_try = [self.primary_model] + [m for m in self.fallback_models if m != self.primary_model]

        for model in models_to_try:
            try:
                if progress_callback:
                    progress_callback(f"Finding clips with Gemini ({model})...", 45)

                prompt = f"{system_instructions}\n\nTRANSCRIPT WITH TIMESTAMPS:\n{compact_transcript}\n"
                
                # Try calling Gemini
                try:
                    raw_json = self._call_gemini_api(prompt, model, progress_callback)
                    parsed = ClipSelectionResponse.model_validate_json(raw_json)
                except Exception as val_err:
                    logger.warning(f"Validation error or parsing issue with {model}: {val_err}. Retrying once with error feedback...")
                    retry_prompt = f"{prompt}\n\nNOTE: Previous attempt failed schema validation with error: {val_err}. Ensure valid JSON matching ClipSelectionResponse exactly."
                    raw_json = self._call_gemini_api(retry_prompt, model, progress_callback)
                    parsed = ClipSelectionResponse.model_validate_json(raw_json)

                if parsed and parsed.clips:
                    logger.info(f"Successfully received {len(parsed.clips)} clips from Gemini {model}.")
                    snapped = snap_to_word_boundaries(parsed.clips, words, min_duration, max_duration)
                    final_clips = deduplicate_and_rank_clips(snapped, effective_target)
                    
                    # Ensure minimum count
                    min_needed = 2 if total_duration < 180 else min(3, effective_target)
                    if len(final_clips) < min_needed:
                        logger.warning(f"Gemini returned only {len(final_clips)} clips. Supplementing with heuristic.")
                        heur = find_clips_heuristic(transcript_data, audio_hints, effective_target, min_duration, max_duration, total_duration)
                        final_clips = deduplicate_and_rank_clips(final_clips + heur, effective_target)
                    
                    return final_clips

            except Exception as e:
                logger.warning(f"Gemini model {model} failed: {e}. Trying next fallback...")

        # If all Gemini models fail, use heuristic fallback
        logger.warning("All Gemini models failed or exhausted quota. Falling back to local heuristic.")
        if progress_callback:
            progress_callback("Gemini unavailable. Generating clips using local heuristic...", 50)
        clips = find_clips_heuristic(transcript_data, audio_hints, effective_target, min_duration, max_duration, total_duration)
        return snap_to_word_boundaries(clips, words, min_duration, max_duration)
        return snap_to_word_boundaries(clips, words, min_duration, max_duration)
