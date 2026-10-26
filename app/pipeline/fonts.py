import os
import requests
import logging
from pathlib import Path
from typing import Dict, Optional
from app.config import Config

logger = logging.getLogger("clipag.fonts")

FONT_URLS = {
    "NotoSans-Bold.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf",
    "NotoSansDevanagari-Bold.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf",
    "NotoSansTelugu-Bold.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansTelugu/NotoSansTelugu-Bold.ttf",
    "NotoSansTamil-Bold.ttf": "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSansTamil/NotoSansTamil-Bold.ttf",
}

# Mapping of Whisper language codes to primary font family names
LANG_TO_FONT_FAMILY = {
    "te": "Noto Sans Telugu",
    "hi": "Noto Sans Devanagari",
    "mr": "Noto Sans Devanagari",
    "ne": "Noto Sans Devanagari",
    "sa": "Noto Sans Devanagari",
    "ta": "Noto Sans Tamil",
    "en": "Noto Sans",
}

FALLBACK_FONTS_WINDOWS = {
    "te": ["Gautami", "Nirmala UI", "Arial"],
    "hi": ["Nirmala UI", "Mangal", "Arial"],
    "mr": ["Nirmala UI", "Mangal", "Arial"],
    "ta": ["Nirmala UI", "Latha", "Arial"],
    "en": ["Arial", "Segoe UI", "Tahoma"],
}

def ensure_font(filename: str, url: str) -> Optional[Path]:
    """Downloads a font file to Config.FONTS_DIR if not present."""
    dest = Config.FONTS_DIR / filename
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    
    try:
        logger.info(f"Downloading font {filename} from {url}...")
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            with open(dest, "wb") as f:
                f.write(resp.content)
            logger.info(f"Successfully saved {filename} ({len(resp.content)} bytes)")
            return dest
        else:
            logger.warning(f"Failed to download font {filename}: HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"Error downloading font {filename}: {e}")
    return dest if dest.exists() else None

def ensure_all_fonts() -> Dict[str, Optional[Path]]:
    """Ensures core Noto Sans fonts are downloaded for multilingual captioning."""
    results = {}
    for filename, url in FONT_URLS.items():
        results[filename] = ensure_font(filename, url)
    return results

def get_font_family_for_lang(lang_code: Optional[str]) -> str:
    """Returns the best font family name for ASS subtitles given a language code."""
    if not lang_code:
        return "Noto Sans"
    
    lang = lang_code.lower().strip()
    if lang in LANG_TO_FONT_FAMILY:
        return LANG_TO_FONT_FAMILY[lang]
    
    return "Noto Sans"

def check_fonts_installed() -> bool:
    """Check if either Noto fonts or fallback fonts are present on the system."""
    ensure_all_fonts()
    # Check if at least Latin and Devanagari/Telugu exist in fonts/ or Windows Fonts
    has_local = (Config.FONTS_DIR / "NotoSans-Bold.ttf").exists()
    has_windows = Path("C:/Windows/Fonts/Nirmala.ttc").exists() or Path("C:/Windows/Fonts/arial.ttf").exists()
    return has_local or has_windows
