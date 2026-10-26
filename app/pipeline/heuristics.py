import re
import logging
from typing import List, Dict, Any, Optional
from app.llm.client import ClipCandidate

logger = logging.getLogger("clipag.heuristics")

HOOK_KEYWORDS = {
    "secret", "never", "best", "why", "how", "truth", "warning", "tip",
    "mistake", "actually", "important", "remember", "crazy", "huge", "shocking",
    "rule", "first", "always", "stop", "key", "number one", "advice"
}

def find_clips_heuristic(
    transcript_data: Dict[str, Any],
    audio_hints: Optional[Dict[str, Any]] = None,
    target_clips: int = 6,
    min_duration: int = 20,
    max_duration: int = 60,
    total_video_duration: Optional[float] = None,
) -> List[ClipCandidate]:
    """
    Intelligent heuristic clip selector when Gemini API is unavailable.
    Uses sentence boundaries, question/exclamation marks, hook keywords,
    speech pacing, and audio energy peaks to pick engaging 20-60s reels.
    """
    segments = transcript_data.get("segments", [])
    if not segments:
        # Fallback if no speech was detected: split video into evenly spaced windows
        duration = total_video_duration or transcript_data.get("duration", 180.0)
        clip_len = min(30.0, max(20.0, duration / max(1, target_clips)))
        step = max(clip_len, duration / target_clips) if target_clips > 0 else 30.0
        fallback_clips = []
        for i in range(target_clips):
            s = round(i * step, 2)
            e = round(min(duration, s + clip_len), 2)
            if e - s >= 10.0:
                fallback_clips.append(ClipCandidate(
                    start=s,
                    end=e,
                    title=f"Viral Highlight #{i+1}",
                    hook_line="Watch this exciting moment!",
                    reason="Visual motion and energetic sequence selected.",
                    score=75 - (i * 2),
                    suggested_hashtags=["#reels", "#shorts", "#viral", "#trending", "#fyp"],
                    description=f"Highlight moment {i+1} from the video.",
                ))
        return fallback_clips

    # Extract peak times from audio hints
    peak_times = set()
    if audio_hints and "peak_moments" in audio_hints:
        for p in audio_hints["peak_moments"]:
            peak_times.add(p.get("time", 0.0))

    candidates: List[Dict[str, Any]] = []

    # Slide over segments to create candidate windows
    n = len(segments)
    for i in range(n):
        seg_start = segments[i]["start"]
        accumulated_text = []
        word_count = 0
        
        for j in range(i, n):
            seg_end = segments[j]["end"]
            dur = seg_end - seg_start
            accumulated_text.append(segments[j]["text"])
            word_count += len(segments[j].get("words", [])) or len(segments[j]["text"].split())

            if dur < min_duration:
                continue
            if dur > max_duration:
                break

            # Check if this window ends on a natural punctuation boundary
            text_so_far = " ".join(accumulated_text).strip()
            ends_cleanly = text_so_far.endswith((".", "!", "?", "...", ":"))

            # Calculate score
            score = 50.0

            # 1. Speech pacing score (optimal: 120 - 180 words/min)
            wpm = (word_count / dur) * 60.0
            if 110 <= wpm <= 190:
                score += 15.0
            elif wpm < 80:
                score -= 10.0

            # 2. Curiosity & emotional hooks
            q_count = text_so_far.count("?")
            excl_count = text_so_far.count("!")
            score += min(20.0, q_count * 10.0 + excl_count * 6.0)

            # 3. Keyword matches in opening segment (strong hook)
            opening_lower = segments[i]["text"].lower()
            keyword_hits = sum(1 for kw in HOOK_KEYWORDS if kw in opening_lower)
            score += min(15.0, keyword_hits * 5.0)

            # 4. Audio energy peak alignment
            for pt in peak_times:
                if seg_start <= pt <= seg_end:
                    score += 10.0
                    break

            if ends_cleanly:
                score += 10.0

            candidates.append({
                "start": round(seg_start, 2),
                "end": round(seg_end, 2),
                "text": text_so_far,
                "first_line": segments[i]["text"].strip(),
                "score": min(98, max(50, int(score))),
            })

    duration_val = total_video_duration or transcript_data.get("duration", 0.0) or (segments[-1]["end"] if segments else 0.0)
    selected: List[Dict[str, Any]] = []

    # For long videos / podcasts (>10 minutes), distribute clips across chronological zones
    # so reels cover the entire 1-3 hour conversation rather than clustering in one segment
    if duration_val > 600.0 and target_clips > 1:
        zone_len = duration_val / target_clips
        for z_idx in range(target_clips):
            z_start = z_idx * zone_len
            z_end = min(duration_val, (z_idx + 1) * zone_len)
            
            zone_candidates = [
                c for c in candidates
                if z_start <= c["start"] < z_end or (z_idx == target_clips - 1 and c["start"] >= z_start)
            ]
            zone_candidates.sort(key=lambda x: x["score"], reverse=True)
            
            for cand in zone_candidates:
                overlap = any(
                    max(0.0, min(cand["end"], s["end"]) - max(cand["start"], s["start"])) > 5.0
                    for s in selected
                )
                if not overlap:
                    selected.append(cand)
                    break

    # Fill any remaining slots with highest scoring non-overlapping candidates
    candidates.sort(key=lambda x: x["score"], reverse=True)
    for cand in candidates:
        if len(selected) >= target_clips:
            break
        overlap = any(
            max(0.0, min(cand["end"], s["end"]) - max(cand["start"], s["start"])) > 5.0
            for s in selected
        )
        if not overlap and cand not in selected:
            selected.append(cand)

    # If we still have fewer than target_clips, fill with non-overlapping spaces
    if len(selected) < min(5, target_clips) and candidates:
        for cand in candidates:
            if len(selected) >= target_clips:
                break
            if cand not in selected:
                overlap = any(
                    max(0.0, min(cand["end"], s["end"]) - max(cand["start"], s["start"])) > 8.0
                    for s in selected
                )
                if not overlap:
                    selected.append(cand)

    # Format into ClipCandidate objects
    results = []
    for idx, c in enumerate(selected):
        # Generate punchy title from first line
        raw_title = re.sub(r"[^\w\s]", "", c["first_line"]).strip()
        words = raw_title.split()
        title = " ".join(words[:6]).title() if len(words) >= 3 else f"Must Watch Moment #{idx+1}"
        if not title:
            title = f"Clip Highlight #{idx+1}"

        results.append(ClipCandidate(
            start=c["start"],
            end=c["end"],
            title=title,
            hook_line=c["first_line"][:100],
            reason="High speech density with natural sentence flow and emotional indicators.",
            score=c["score"],
            suggested_hashtags=["#reels", "#shorts", "#viral", "#insights", "#foryou"],
            description=f"{title} - Don't miss this key takeaway!",
        ))

    # If still fewer than target_clips, fill with spaced windows across total video duration
    needed = target_clips - len(results)
    if needed > 0:
        duration = total_video_duration or transcript_data.get("duration", 180.0)
        clip_span = min(30.0, max(float(min_duration), 20.0))
        curr_s = 0.0

        while len(results) < target_clips and (curr_s + 15.0) <= duration:
            curr_e = min(duration, curr_s + clip_span)
            overlap = any(
                max(0.0, min(curr_e, r.end) - max(curr_s, r.start)) > 3.0
                for r in results
            )
            if not overlap and (curr_e - curr_s) >= 15.0:
                results.append(ClipCandidate(
                    start=round(curr_s, 2),
                    end=round(curr_e, 2),
                    title=f"Viral Highlight #{len(results) + 1}",
                    hook_line="Check out this exciting moment!",
                    reason="High activity segment with continuous visual and audio pacing.",
                    score=max(50, 75 - (len(results) * 3)),
                    suggested_hashtags=["#reels", "#shorts", "#viral", "#trending", "#fyp"],
                    description=f"Highlight moment #{len(results) + 1} from the video.",
                ))
                curr_s = curr_e
            else:
                curr_s += 2.0

    # Secondary guarantee: if still fewer than target_clips, create evenly spaced slices
    if len(results) < target_clips:
        duration = total_video_duration or transcript_data.get("duration", 180.0)
        step = max(10.0, (duration - 20.0) / max(1, target_clips))
        for i in range(target_clips):
            if len(results) >= target_clips:
                break
            s = round(i * step, 2)
            e = round(min(duration, s + 22.0), 2)
            if (e - s) >= 10.0:
                results.append(ClipCandidate(
                    start=s,
                    end=e,
                    title=f"Viral Highlight #{len(results) + 1}",
                    hook_line="Check out this exciting moment!",
                    reason="High activity segment with continuous visual and audio pacing.",
                    score=max(50, 70 - (len(results) * 3)),
                    suggested_hashtags=["#reels", "#shorts", "#viral", "#trending", "#fyp"],
                    description=f"Highlight moment #{len(results) + 1} from the video.",
                ))

    logger.info(f"Heuristic clip finder generated {len(results)} clips.")
    return results
