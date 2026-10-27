import os
import sys
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"

def load_yaml_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

_CONFIG = load_yaml_config()

# Logging configuration
LOG_FILE = _CONFIG.get("app", {}).get("log_file", "app.log")
LOG_PATH = BASE_DIR / LOG_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)

logger = logging.getLogger("clipag")

def detect_device_and_compute() -> tuple[str, str]:
    """Auto-detects optimal hardware: CUDA GPU on Google Colab / servers, or CPU."""
    env_device = os.getenv("WHISPER_DEVICE")
    env_compute = os.getenv("WHISPER_COMPUTE_TYPE")
    if env_device:
        return env_device, env_compute or ("float16" if env_device == "cuda" else "int8")

    yaml_device = _CONFIG.get("transcription", {}).get("device", "auto")
    yaml_compute = _CONFIG.get("transcription", {}).get("compute_type", "auto")
    if yaml_device and yaml_device != "auto":
        return yaml_device, yaml_compute if yaml_compute != "auto" else ("float16" if yaml_device == "cuda" else "int8")

    # Auto-detect CUDA
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            logger.info("⚡ CUDA GPU detected! Using 'cuda' device with 'float16' compute.")
            return "cuda", "float16"
    except Exception:
        pass

    return "cpu", "int8"

_opt_device, _opt_compute = detect_device_and_compute()

class Config:
    BASE_DIR: Path = BASE_DIR
    WORK_DIR: Path = BASE_DIR / _CONFIG.get("app", {}).get("work_dir", "work")
    OUTPUTS_DIR: Path = BASE_DIR / _CONFIG.get("app", {}).get("outputs_dir", "outputs")
    FONTS_DIR: Path = BASE_DIR / _CONFIG.get("app", {}).get("fonts_dir", "fonts")
    
    # App
    HOST: str = _CONFIG.get("app", {}).get("host", "127.0.0.1")
    PORT: int = int(_CONFIG.get("app", {}).get("port", 8000))
    
    # Ingest
    MAX_HEIGHT: int = _CONFIG.get("ingest", {}).get("max_height", 1080)
    AUDIO_SAMPLE_RATE: int = _CONFIG.get("ingest", {}).get("audio_sample_rate", 16000)
    AUDIO_CHANNELS: int = _CONFIG.get("ingest", {}).get("audio_channels", 1)
    
    # Transcription
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", _CONFIG.get("transcription", {}).get("model", "small"))
    WHISPER_DEVICE: str = _opt_device
    WHISPER_COMPUTE_TYPE: str = _opt_compute
    WHISPER_VAD_FILTER: bool = _CONFIG.get("transcription", {}).get("vad_filter", True)
    WHISPER_CHUNK_SECONDS: float = float(os.getenv("WHISPER_CHUNK_SECONDS", _CONFIG.get("transcription", {}).get("chunk_seconds", 300.0)))
    WHISPER_WORKERS: int = int(os.getenv("WHISPER_WORKERS", _CONFIG.get("transcription", {}).get("workers", 2)))
    
    # LLM
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", _CONFIG.get("llm", {}).get("default_model", "gemini-2.5-flash")).strip()
    
    _fallback_env = os.getenv("GEMINI_FALLBACK_MODELS", "")
    if _fallback_env:
        GEMINI_FALLBACK_MODELS: List[str] = [m.strip() for m in _fallback_env.split(",") if m.strip()]
    else:
        GEMINI_FALLBACK_MODELS: List[str] = _CONFIG.get("llm", {}).get("fallback_models", ["gemini-1.5-flash", "gemini-2.0-flash"])
        
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", _CONFIG.get("llm", {}).get("provider", "gemini")).strip()
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "").strip()
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    
    RPM_LIMIT: int = int(_CONFIG.get("llm", {}).get("rpm_limit", 10))
    MAX_RETRIES: int = int(_CONFIG.get("llm", {}).get("max_retries", 5))
    TEMPERATURE: float = float(_CONFIG.get("llm", {}).get("temperature", 0.3))
    TARGET_CLIPS: int = int(_CONFIG.get("llm", {}).get("target_clips", 6))
    MIN_CLIPS: int = int(_CONFIG.get("llm", {}).get("min_clips", 5))
    MIN_CLIP_DURATION: int = int(_CONFIG.get("llm", {}).get("min_clip_duration", 20))
    MAX_CLIP_DURATION: int = int(_CONFIG.get("llm", {}).get("max_clip_duration", 60))
    CHUNK_DURATION_MINUTES: int = int(_CONFIG.get("llm", {}).get("chunk_duration_minutes", 20))
    
    # Reframing
    TARGET_WIDTH: int = _CONFIG.get("reframing", {}).get("target_width", 1080)
    TARGET_HEIGHT: int = _CONFIG.get("reframing", {}).get("target_height", 1920)
    SAMPLE_FPS: float = float(_CONFIG.get("reframing", {}).get("sample_fps", 1.5))
    SMOOTHING_WINDOW: int = _CONFIG.get("reframing", {}).get("smoothing_window", 15)
    MAX_PAN_SPEED_PX_PER_SEC: int = _CONFIG.get("reframing", {}).get("max_pan_speed_px_per_sec", 250)
    MIN_SPEAKER_HOLD_SEC: float = float(_CONFIG.get("reframing", {}).get("min_speaker_hold_sec", 2.0))
    NO_FACE_MODE: str = _CONFIG.get("reframing", {}).get("no_face_mode", "auto")
    
    # Rendering
    PRESET: str = _CONFIG.get("rendering", {}).get("preset", "veryfast")
    FPS: int = int(_CONFIG.get("rendering", {}).get("fps", 30))
    TARGET_LUFS: float = float(_CONFIG.get("rendering", {}).get("target_lufs", -14.0))
    FADE_DURATION: float = float(_CONFIG.get("rendering", {}).get("fade_duration", 0.3))
    
    # Styles
    CAPTION_STYLES: Dict[str, Any] = _CONFIG.get("caption_styles", {})

# Ensure directories exist
Config.WORK_DIR.mkdir(parents=True, exist_ok=True)
Config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
Config.FONTS_DIR.mkdir(parents=True, exist_ok=True)
