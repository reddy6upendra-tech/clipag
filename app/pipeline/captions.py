import os
import re
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import Config
from app.pipeline.fonts import get_font_family_for_lang

logger = logging.getLogger("clipag.captions")

def normalize_ass_color(color_str: str) -> str:
    """Ensures an ASS color string is formatted as &HBBGGRR& or &HAABBGGRR& without extra ampersands."""
    c = color_str.strip().strip("&")
    if not c.startswith("H") and not c.startswith("h"):
        c = "H" + c
    return f"&{c}&"

def escape_ffmpeg_path(path: Path) -> str:
    """Escapes Windows path for FFmpeg filter arguments (subtitles filter)."""
    p_str = str(path.resolve()).replace("\\", "/")
    # Escape colon for drive letter
    return p_str.replace(":", "\\:")

def format_ass_timestamp(seconds: float) -> str:
    """Converts seconds into ASS timestamp format: H:MM:SS.cs (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def group_words_into_lines(
    words: List[Dict[str, Any]],
    words_per_line: int = 3,
    max_chars_per_line: int = 24,
) -> List[List[Dict[str, Any]]]:
    """
    Groups words into short, readable 2-4 word lines for vertical reels.
    Breaks lines on punctuation or when character limit is reached to avoid edge clipping.
    """
    lines = []
    curr_line = []
    curr_chars = 0
    
    for w in words:
        txt = w.get("word", "").strip()
        added_len = len(txt) + (1 if curr_line else 0)
        
        # Check if line would exceed max characters
        would_exceed_chars = (curr_chars + added_len > max_chars_per_line) and len(curr_line) >= 2
        
        if would_exceed_chars or len(curr_line) >= words_per_line:
            lines.append(curr_line)
            curr_line = [w]
            curr_chars = len(txt)
        else:
            curr_line.append(w)
            curr_chars += added_len
            
        # End line immediately if word ends with terminal punctuation
        if txt.endswith((".", "!", "?", ":", ";", "-")) and len(curr_line) >= 2:
            lines.append(curr_line)
            curr_line = []
            curr_chars = 0
            
    if curr_line:
        lines.append(curr_line)
        
    return lines

def generate_ass_subtitles(
    words: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    output_ass_path: Path,
    style_name: str = "bold_yellow",
    language: Optional[str] = None,
    position: str = "bottom",
    font_size_override: Optional[int] = None,
) -> Path:
    """
    Generates word-by-word animated ASS subtitles for a clip.
    Current spoken word has an eye-catching scale pop animation and vibrant color highlight.
    Positions captions safely away from Reels UI (bottom 22% or center).
    """
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    
    styles = Config.CAPTION_STYLES
    style_cfg = styles.get(style_name, styles.get("bold_yellow", {}))
    
    font_family = get_font_family_for_lang(language)
    font_size = font_size_override or style_cfg.get("font_size", 78)
    normal_color = normalize_ass_color(style_cfg.get("normal_color", "&H00FFFFFF"))
    highlight_color = normalize_ass_color(style_cfg.get("highlight_color", "&H0000E5FF"))
    outline_color = normalize_ass_color(style_cfg.get("outline_color", "&H00000000"))
    outline_width = style_cfg.get("outline_width", 5)
    shadow_width = style_cfg.get("shadow_width", 3)
    words_per_line = style_cfg.get("words_per_line", 3)
    bottom_margin_pct = style_cfg.get("bottom_margin_percent", 22)
    
    # Configure alignment and vertical margin:
    # ASS alignments: 2 = bottom-center; 5 = middle-center; 8 = top-center
    pos_lower = (position or "bottom").lower()
    if pos_lower == "center":
        alignment = 5
        margin_v = 0
    elif pos_lower == "upper_third" or pos_lower == "top":
        alignment = 8
        margin_v = int(round(1920 * 0.20)) # 384px from top
    elif pos_lower == "mid_low":
        alignment = 2
        margin_v = int(round(1920 * 0.35)) # 672px from bottom (just below center)
    else: # "bottom" (Lower third - default)
        alignment = 2
        margin_v = int(round(1920 * (bottom_margin_pct / 100.0)))
    
    # Filter words relevant to this clip and shift timestamps
    clip_words = []
    for w in words:
        w_start = w["start"]
        w_end = w["end"]
        if w_end > clip_start and w_start < clip_end:
            # Shift relative to clip start
            rel_start = max(0.0, round(w_start - clip_start, 3))
            rel_end = max(rel_start + 0.05, min(clip_end - clip_start, round(w_end - clip_start, 3)))
            clip_words.append({
                "word": w["word"].strip(),
                "start": rel_start,
                "end": rel_end,
            })
            
    # Group words into lines
    lines = group_words_into_lines(clip_words, words_per_line=words_per_line, max_chars_per_line=24)
    
    # ASS Header
    ass_content = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_family},{font_size},{normal_color},{highlight_color},{outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{outline_width},{shadow_width},{alignment},40,40,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    
    # Generate animated word-by-word dialogue events
    num_lines = len(lines)
    for line_idx, line in enumerate(lines):
        if not line:
            continue
        line_start = line[0]["start"]
        line_end = line[-1]["end"]
        
        # Smooth small gaps to prevent subtitle blinking between lines
        if line_idx < num_lines - 1 and lines[line_idx + 1]:
            next_start = lines[line_idx + 1][0]["start"]
            if 0 < (next_start - line_end) <= 0.35:
                line_end = next_start
        
        # For each word in this line, create an active highlight slice
        for i, target_word in enumerate(line):
            w_start = target_word["start"]
            
            # Bridge word transitions cleanly within the line
            if i < len(line) - 1:
                w_end = line[i + 1]["start"]
            else:
                w_end = max(target_word["end"], line_end)
                
            if w_end <= w_start:
                w_end = w_start + 0.1
                
            start_str = format_ass_timestamp(w_start)
            end_str = format_ass_timestamp(w_end)
            
            # Construct line text where target_word pops with scale & highlight color
            tokens = []
            for j, w in enumerate(line):
                txt = w["word"].upper() if language in ("en", None) else w["word"]
                if j == i:
                    # Active word pop: scale to 112%, bold, vibrant highlight color
                    tokens.append(f"{{\\c{highlight_color}\\b1\\fscx112\\fscy112}}{txt}{{\\c{normal_color}\\b0\\fscx100\\fscy100}}")
                else:
                    tokens.append(f"{{\\c{normal_color}}}{txt}")
                    
            dialogue_text = " ".join(tokens)
            ass_content.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{dialogue_text}")

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_content))
        
    logger.info(f"Generated ASS subtitles with {len(lines)} lines at {output_ass_path.name}")
    return output_ass_path

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_content))
        
    logger.info(f"Generated ASS subtitles with {len(lines)} lines at {output_ass_path.name}")
    return output_ass_path
