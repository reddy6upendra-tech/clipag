# 🎬 AI Reels Clipper (Local MVP)

An end-to-end, local-first AI system that converts long videos (YouTube URLs or local files) into vertical (**1080x1920**) reels ready for Instagram Reels, YouTube Shorts, and TikTok. Each clip features burned-in, word-by-word animated captions, face-centered reframing, and professional audio normalization.

Designed to run efficiently on a standard laptop **with NO GPU required**.

---

## ⚡ Quickstart (Setup in under 10 commands)

```bash
# 1. Clone / open directory
cd clipag

# 2. (Optional) Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
# source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment configuration
cp .env.example .env

# 5. Run system diagnostics (verifies FFmpeg, fonts, and API connectivity)
python -m app.doctor

# 6. Start the application
# On Windows:
run.bat
# On Linux/macOS:
# ./run.sh
```

Then open your browser at **`http://127.0.0.1:8000`**.

---

## 🔑 Gemini API Key & Model Configuration

1. Get a free API key at [Google AI Studio](https://aistudio.google.com/).
2. Open `.env` and add your key:
   ```env
   GEMINI_API_KEY=AIzaSy...
   GEMINI_MODEL=gemini-2.5-flash
   GEMINI_FALLBACK_MODELS=gemini-1.5-flash,gemini-2.0-flash
   ```
3. **Offline / Fallback Mode**: If no API key is provided, the system automatically uses the intelligent heuristic selector (analyzing speech density, question/exclamation cues, emotional hook keywords, and audio energy peaks) to produce viral clips without internet access.
4. **Privacy Guaranteed**: Only compact text transcripts and audio energy hints are sent to the Gemini API. **Video files are NEVER uploaded to the cloud.**

---

## 🚀 How to Run

### Option 1: Web UI (Browser Dashboard)
Run `run.bat` (Windows) or `./run.sh` (Linux/Mac), or run manually:
```bash
python -m uvicorn app.api.server:app --host 127.0.0.1 --port 8000 --reload
```
Open `http://127.0.0.1:8000` to:
- Paste a YouTube URL or drag-and-drop a local video file
- Select number of reels (5 to 10), caption style preset, and Whisper model
- Watch real-time progress across all pipeline stages
- Preview generated 9:16 vertical reels in the browser
- Fine-tune clip boundaries with the **Adjust Timing (±2s)** control and re-render only that clip
- Download individual MP4 reels or download the complete package (.ZIP)

### Option 2: Command Line Interface (CLI)
```bash
python -m app.cli --input "https://www.youtube.com/watch?v=YOUR_VIDEO" --clips 6
# Or on a local video:
python -m app.cli --input "path/to/podcast.mp4" --clips 6 --style bold_yellow
```

### Option 3: System Doctor
Check FFmpeg, font installation, and Gemini API connectivity:
```bash
python -m app.doctor
```

---

## ⚙️ Speed vs. Quality Tuning on CPU

| Setting in `config.yaml` / UI | Default | Faster (Ultra-Fast CPU) | Maximum Accuracy |
| :--- | :--- | :--- | :--- |
| **Whisper Model** | `small` | `tiny` / `base` (3-5x faster) | `medium` (best multilingual) |
| **FFmpeg Preset** | `ultrafast` | `ultrafast` | `veryfast` |
| **Reframing Sample FPS**| `1.5` | `1.0` | `3.0` |
| **Target Resolution** | `1080x1920` | `720x1280` | `1080x1920` |

### 💡 Multilingual Long Podcasts (Telugu, Hindi, etc.)
- **Why `base` model struggled on long non-English videos**: Faster-Whisper's auto-detect evaluates only the first 30 seconds of audio. If a podcast starts with English intro phrases ("Welcome back to the show"), sponsor mentions, or background music, auto-detect classifies the entire video as English. Once locked into English, Whisper tries to force non-English speech into English words or romanized gibberish. Furthermore, `base` (74M parameters) lacks the token vocabulary required for nuanced Telugu/Hindi script rendering.
- **The Solution**: 
  1. **Select Language Explicitly**: Choose `Telugu` or `Hindi` in the UI dropdown or CLI (`--language te`). This forces Whisper to decode strictly in that script, ignoring intro English words.
  2. **Use the `small` Model**: At 244M parameters, `small` is the sweet spot for laptop CPUs—delivering 10x better phonetic accuracy and proper script spelling in Telugu and Hindi while remaining 4x faster than real-time.
1. **Ultra-Fast FFmpeg Demuxer Seeking**: Frame extraction for face analysis uses keyframe seeking (`-ss` before `-i`), reducing face analysis from 30+ minutes down to **under 3 seconds** per clip.
2. **Prioritized H.264/AVC Streams**: Downloads prefer native H.264 video streams over CPU-heavy AV1/VP9 codecs, allowing 10x-15x faster decoding on laptop CPUs.
3. **Downscaled Single-Pass Background Blur**: Background fit rendering scales down to 270x480 before single-pass blur, speeding up rendering by **23x** compared to full-resolution boxblur.
4. **Greedy Whisper Search**: Configured `beam_size=1`, multi-threaded CPU execution, and VAD silence filtering to cut transcription time in half.

---

## 🎨 Caption Style Presets

Configured in `config.yaml`:
1. **Bold Yellow (Reels Pop)**: High-contrast white text with vibrant yellow active word highlight and black border.
2. **Punchy Neon (MrBeast Vibe)**: High-impact white text with neon green/cyan highlight.
3. **Cyber Cyan (Clean Tech)**: Sleek modern silver text with cyan highlight.

All captions maintain the bottom ~22% margin free from overlapping Instagram/TikTok UI controls (likes, comments, captions, sound banner).

### Multilingual & Indian Script Support
Automatic font fallback system with bundled Google Noto Sans fonts:
- **Telugu**: `Noto Sans Telugu` / `Gautami`
- **Hindi / Devanagari**: `Noto Sans Devanagari` / `Nirmala UI`
- **Tamil**: `Noto Sans Tamil`
- **Latin / English**: `Noto Sans` / `Arial`

---

## 📐 Architecture & Key Decisions

- **Official `google-genai` SDK**: Uses the latest official Python SDK (`from google import genai`) with structured Pydantic schema validation (`response_schema=ClipSelectionResponse`), exponential backoff on 429/5xx, and a global RPM rate limiter.
- **Resumable Caching**: Every stage (ingest, audio extraction, Whisper transcription, clip analysis, and rendering) is cached by content hash in `work/<hash>/`. Re-running skips completed stages immediately.
- **Smoothed 9:16 Reframing**: MediaPipe / OpenCV face detection sampled at 3 fps with moving average smoothing and maximum velocity clamping (250 px/s) to ensure smooth camera movement. Features active speaker hold (2.0s minimum) and an automatic blurred-background fit layout for clips without faces.
- **Audio Mastering**: Normalizes loudness to standard broadcast/social target (~-14 LUFS) with subtle 50ms micro-fades at boundaries to prevent speaker muffle while avoiding DC audio clicks.
- **Natural Breathing Buffers**: Extends render start by -0.10s and end by +0.25s around speech boundaries so vocal breathing and trailing phonemes are preserved.
- **Deadzone Tripod Stabilization**: 45px motion deadzone eliminates camera micro-jitter on seated speakers while preserving smooth panning when subjects move.
- **Dynamic Animated Captions**: Word-level highlight pop animation (`\fscx112\fscy112`), 24-character intelligent line breaks with punctuation awareness, and 350ms gap bridging to eliminate caption blink.
- **2–3 Hour Podcast Support**:
  - **Timeline Zone Partitioning**: Long videos (>10 minutes) automatically divide the full timeline into equal chronological zones, ensuring viral moments are selected evenly across the entire podcast (intro, discussion, deep dive, conclusion) rather than clustering in the first 15 minutes.
  - **Live Elapsed / Total Duration Tracking**: Real-time progress displays `HH:MM:SS / HH:MM:SS (X%)` during transcription.
  - **Whisper Speed Recommendation**: For 2-3 hour podcasts on a CPU laptop, select `base` or `tiny` Whisper model in the UI dropdown or CLI (`--model base`) for 5x faster processing without loss of caption sync.
- **FIFO Job Queue**: Dedicated single-worker FIFO queue (`queue.Queue`) ensures only 1 CPU-heavy video is processed at a time. Multiple submissions are queued gracefully with position tracking, preventing CPU thrashing or site crashes.
- **Language Selector & Multilingual Accuracy**: Dropdown for Telugu (`te`), Hindi (`hi`), English (`en`), Tamil (`ta`), etc. Explicitly forcing language mode prevents Whisper from misidentifying non-English dialogue as English (or translating), ensuring authentic native script spelling.
- **"Come Back Later" Direct Link & LocalStorage History**: Each job is accessible at `http://localhost:8000/?job=<job_id>`. Users processing 2+ hr podcasts can close their tab and return anytime. Recent jobs are auto-saved in browser history.
- **Automated Lifecycle & Disk Cleanup**: Background daemon purges intermediate temp files and deletes jobs older than 24 hours to prevent disk exhaustion.
- **Interactive Trimmer & Clip Swap**: Fine-tune clip start/end boundaries with precision second inputs or swap any reel with the next best viral moment from the video with a single click.
- **Graceful Failure Handling**: Actionable error diagnosis for private videos, age-gated videos, missing audio streams, videos shorter than 3 seconds, or audio without spoken dialogue.
- **Packaging**: Automatically outputs per-reel metadata (`metadata/clip_01.json`), combined CSV (`metadata/reels_summary.csv`), and a complete `.zip` archive containing all reels and metadata.

---

## 🧪 Running Tests

Execute the complete test suite:
```bash
python -m pytest -v
```

All 21 unit and integration tests verify:
- Sentence/word boundary snapping (`tests/test_llm_gemini.py`)
- Non-overlapping clip deduplication and ranking (`tests/test_llm_gemini.py`)
- Gemini structured output validation, retry with error feedback, and 429 backoff (`tests/test_llm_gemini.py`)
- Heuristic fallback clip generation (`tests/test_llm_gemini.py`)
- Ingestion and audio extraction caching (`tests/test_ingest.py`)
- Whisper transcription and word-level timestamps (`tests/test_transcribe.py`)
- 9:16 crop smoothing and blurred fit rendering (`tests/test_reframer.py`)
- ASS subtitle timing and Indian script rendering (`tests/test_captions.py`)
- End-to-end CLI pipeline generating 5+ 1080x1920 MP4 reels (`tests/test_cli.py`)
- FastAPI web service, job polling, adjust endpoint, and zip download (`tests/test_api.py`)

---

## 📝 Known Limitations

1. **CPU Render Time**: Rendering multiple high-definition 1080x1920 video streams with subtitle burn-in and loudnorm on CPU typically processes at ~0.5x–0.8x real-time speed. A 30-minute source video processing 5 reels finishes in approximately 12–18 minutes on modern multi-core laptop CPUs.
2. **Extreme Noise / Overlapping Speech**: When multiple speakers talk simultaneously, Whisper transcribes the dominant speaker; the face reframer tracks the largest face or holds the previous speaker for at least 2 seconds.
