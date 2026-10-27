import argparse
import sys
import json
import logging
from pathlib import Path
from typing import Optional, Any, Dict, List
from app.config import Config

# Ensure Windows terminal outputs UTF-8 cleanly with immediate flushing
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
from app.pipeline.ingest import ingest_source
from app.pipeline.transcribe import transcribe_audio
from app.llm.gemini import GeminiProvider
from app.pipeline.renderer import render_single_clip, package_reels

logger = logging.getLogger("clipag.cli")

def run_pipeline(
    input_source: str,
    target_clips: int = 0,
    caption_style: str = "bold_yellow",
    caption_position: str = "bottom",
    font_size_override: Optional[int] = None,
    whisper_model: str = "small",
    language: str = "auto",
    task: str = "translate",
    gemini_model: Optional[str] = None,
    force_recompute: bool = False,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """Executes the complete AI Reels Clipper pipeline end-to-end."""
    print("\n================== AI REELS CLIPPER PIPELINE ==================\n")
    print(f"[*] Input: {input_source}")
    print(f"[*] Target Clips: {target_clips or 'Auto (Dynamic based on video length)'}")
    print(f"[*] Caption Style: {caption_style} (Position: {caption_position})")
    print(f"[*] Whisper Model: {whisper_model} (Language: {language}, Task: {task})")

    # 1. Ingest
    if progress_callback:
        progress_callback("Ingesting video source...", 5)
    ingest_res = ingest_source(input_source, force_recompute=force_recompute, progress_callback=progress_callback)
    job_id = ingest_res["job_id"]
    job_dir = Path(ingest_res["job_dir"])
    video_path = Path(ingest_res["video_path"])
    audio_path = Path(ingest_res["audio_path"])
    print(f"[OK] Ingested video (Job ID: {job_id}, Resolution: {ingest_res['metadata']['width']}x{ingest_res['metadata']['height']}, Duration: {ingest_res['metadata']['duration']}s)")

    # 2. Transcription
    if progress_callback:
        progress_callback(f"Transcribing speech ({whisper_model}, {language})...", 20)
    transcript_data = transcribe_audio(
        audio_path=audio_path,
        job_dir=job_dir,
        model_name=whisper_model,
        language=language,
        task=task,
        force_recompute=force_recompute,
        progress_callback=progress_callback,
    )
    print(f"[OK] Transcribed {len(transcript_data['words'])} words in language '{transcript_data['language']}' (task: {task})")

    # 3. Clip Selection
    if progress_callback:
        progress_callback("Finding viral clips with Gemini / AI Heuristics...", 40)
    provider = GeminiProvider()
    if gemini_model:
        provider.primary_model = gemini_model
        
    clips = provider.find_clips(
        transcript_data=transcript_data,
        audio_hints=transcript_data.get("audio_energy"),
        target_clips=target_clips,
        min_duration=Config.MIN_CLIP_DURATION,
        max_duration=Config.MAX_CLIP_DURATION,
        total_video_duration=ingest_res["metadata"]["duration"],
        progress_callback=progress_callback,
    )
    
    # Save clips.json
    clips_json_path = job_dir / "clips.json"
    with open(clips_json_path, "w", encoding="utf-8") as f:
        json.dump([c.model_dump() for c in clips], f, indent=2, ensure_ascii=False)
    print(f"[OK] Selected {len(clips)} viral clips. Saved to {clips_json_path.name}")

    # 4. Rendering & Packaging
    output_dir = Config.OUTPUTS_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Also save clips.json to output directory
    with open(output_dir / "clips.json", "w", encoding="utf-8") as f:
        json.dump([c.model_dump() for c in clips], f, indent=2, ensure_ascii=False)

    rendered_clips = []
    total_clips = len(clips)
    for idx, clip in enumerate(clips, start=1):
        step_progress = 50 + int((idx / total_clips) * 45)
        if progress_callback:
            progress_callback(f"Rendering Clip {idx}/{total_clips}: {clip.title[:25]}...", step_progress)
            
        print(f"[*] Rendering Clip {idx}/{total_clips} [{clip.start:.1f}s - {clip.end:.1f}s]: '{clip.title}'...")
        meta = render_single_clip(
            video_path=video_path,
            clip=clip,
            clip_idx=idx,
            output_dir=output_dir,
            words=transcript_data["words"],
            language=transcript_data.get("language"),
            caption_style=caption_style,
            caption_position=caption_position,
            font_size_override=font_size_override,
            force_recompute=force_recompute,
            progress_callback=progress_callback,
        )
        rendered_clips.append(meta)

    # 5. Zip & CSV Packaging
    if progress_callback:
        progress_callback("Packaging reels into ZIP and CSV...", 98)
    zip_path = package_reels(output_dir, rendered_clips)
    print(f"[OK] Generated {len(rendered_clips)} vertical reels in {output_dir}")
    print(f"[OK] Full ZIP Package: {zip_path}")

    if progress_callback:
        progress_callback("Done!", 100)

    print("\n=================== GENERATED REELS SUMMARY ===================\n")
    for r in rendered_clips:
        print(f"  #{r['clip_index']:02d} | Score: {r['score']:2d} | [{r['start_sec']:5.1f}s - {r['end_sec']:5.1f}s] ({r['duration_sec']:4.1f}s) | {r['title']}")
        print(f"       File: {r['filename']}")
        print(f"       Hook: \"{r['hook_line'][:60]}...\"")
    print(f"\nAll files saved to: {output_dir.resolve()}\n")

    return {
        "job_id": job_id,
        "output_dir": str(output_dir),
        "zip_path": str(zip_path),
        "clips": rendered_clips,
    }

def main():
    parser = argparse.ArgumentParser(description="AI Reels Clipper: Convert long videos into viral 9:16 vertical reels with captions.")
    parser.add_argument("--input", "-i", required=True, help="YouTube URL or local video file path")
    parser.add_argument("--clips", "-c", type=int, default=0, help="Target number of clips (default: 0 = Auto dynamic)")
    parser.add_argument("--style", "-s", default="bold_yellow", help="Caption style preset (bold_yellow, beast_red, cyber_cyan, hormozi_bold, fire_crimson, karaoke_gold, emerald_mint, minimal_clean)")
    parser.add_argument("--position", "-p", default="bottom", help="Caption screen position (bottom, mid_low, center, upper_third)")
    parser.add_argument("--font-size", type=int, default=None, help="Caption font size in px (58, 68, 78, 88, 98)")
    parser.add_argument("--language", "-l", default="auto", help="Source audio language (auto, te, hi, en, ta, kn, ml, es)")
    parser.add_argument("--task", default="translate", choices=["translate", "transcribe"], help="translate (translate any language to English captions) or transcribe")
    parser.add_argument("--model", "-m", default="small", help="faster-whisper model (base, small, medium)")
    parser.add_argument("--gemini-model", help="Gemini model name override")
    parser.add_argument("--force", "-f", action="store_true", help="Force recomputation (ignore cache)")

    args = parser.parse_args()
    try:
        run_pipeline(
            input_source=args.input,
            target_clips=args.clips,
            caption_style=args.style,
            caption_position=args.position,
            font_size_override=args.font_size,
            whisper_model=args.model,
            language=args.language,
            task=args.task,
            gemini_model=args.gemini_model,
            force_recompute=args.force,
        )
    except Exception as e:
        logger.exception("Pipeline failed:")
        print(f"\n[ERROR] Pipeline failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
