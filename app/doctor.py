import sys
import shutil
import subprocess
import logging
from pathlib import Path
from app.config import Config
from app.pipeline.fonts import check_fonts_installed, ensure_all_fonts

logger = logging.getLogger("clipag.doctor")

def check_python_version() -> bool:
    v = sys.version_info
    print(f"[*] Python version: {v.major}.{v.minor}.{v.micro}", end=" ")
    if v.major == 3 and v.minor >= 11:
        print("[OK]")
        return True
    else:
        print("[FAIL] - Python 3.11+ required.")
        return False

def check_ffmpeg() -> bool:
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    
    if not ffmpeg_path:
        print("[FAIL] FFmpeg is not found in PATH!")
        print("  -> FIX: Install FFmpeg from https://gyan.dev/ffmpeg/builds/ or run 'winget install Gyan.FFmpeg' and restart your terminal.")
        return False
    
    if not ffprobe_path:
        print("[WARNING] ffprobe not found in PATH! (FFmpeg is present).")
    
    # Check libass support
    try:
        proc = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out = proc.stdout + proc.stderr
        has_libass = "--enable-libass" in out
        print(f"[*] FFmpeg detected at: {ffmpeg_path} (libass support: {'YES' if has_libass else 'NO'}) [OK]")
        if not has_libass:
            print("  -> WARNING: FFmpeg was built without libass. Subtitles may require fallback rendering.")
        return True
    except Exception as e:
        print(f"[FAIL] Error checking FFmpeg: {e}")
        return False

def check_fonts() -> bool:
    print("[*] Checking multilingual fonts...", end=" ")
    try:
        results = ensure_all_fonts()
        ok_count = sum(1 for v in results.values() if v is not None and v.exists())
        print(f"[OK] ({ok_count}/{len(results)} Noto fonts verified in {Config.FONTS_DIR})")
        return True
    except Exception as e:
        print(f"[WARNING] Could not verify/download all fonts: {e}")
        return check_fonts_installed()

def check_gemini_key() -> bool:
    key = Config.GEMINI_API_KEY
    if not key or key == "your_gemini_api_key_here":
        print("[WARNING] GEMINI_API_KEY is not set or using placeholder in .env!")
        print("  -> AI clip selection requires a Google Gemini key.")
        print("  -> FIX: Get a free key at https://aistudio.google.com/ and add 'GEMINI_API_KEY=AIza...' to your .env file.")
        print("  -> NOTE: The system will automatically use the intelligent heuristic fallback if no key is provided.")
        return False
    
    # Mask key for privacy
    masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "***"
    print(f"[*] GEMINI_API_KEY is configured ({masked})")
    print(f"[*] Testing Gemini API connectivity with model: '{Config.GEMINI_MODEL}'...", end=" ")
    
    try:
        from google import genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents="Respond with the single word: OK",
        )
        if response and response.text:
            print(f"[OK] (Response: {response.text.strip()})")
            return True
        else:
            print("[FAIL] Empty response from Gemini API.")
            return False
    except Exception as e:
        err_msg = str(e)
        print(f"[FAIL] API Error: {err_msg}")
        print("  -> FIX: Verify that your API key is valid, active, and has access to Gemini models at https://aistudio.google.com/.")
        print("  -> NOTE: The system will fall back to local heuristic clip selection.")
        return False

def run_diagnostics() -> bool:
    print("\n================ AI REELS CLIPPER: SYSTEM DOCTOR ================\n")
    py_ok = check_python_version()
    ffmpeg_ok = check_ffmpeg()
    fonts_ok = check_fonts()
    gemini_ok = check_gemini_key()
    
    print("\n=================================================================\n")
    all_ok = py_ok and ffmpeg_ok
    if all_ok:
        print("[SUCCESS] Core system requirements satisfied!")
        if not gemini_ok:
            print("[INFO] Running in heuristic fallback mode unless a valid GEMINI_API_KEY is provided.")
    else:
        print("[ERROR] Please resolve the issues marked [FAIL] above before continuing.")
    
    return all_ok

if __name__ == "__main__":
    success = run_diagnostics()
    sys.exit(0 if success else 1)
