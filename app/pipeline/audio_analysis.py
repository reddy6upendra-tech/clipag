import wave
import numpy as np
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("clipag.audio_analysis")

def analyze_audio_energy(wav_path: Path, bucket_sec: float = 30.0) -> Dict[str, Any]:
    """
    Analyzes a 16 kHz mono WAV file to extract:
    - Audio energy profile over time (RMS in dB)
    - Silence gaps (pauses)
    - Peak energy segments (climax/hype moments)
    """
    if not wav_path.exists():
        return {"buckets": [], "silence_gaps": [], "peak_moments": []}

    try:
        with wave.open(str(wav_path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            
            raw_data = wf.readframes(n_frames)
            
        if sampwidth == 2:
            dtype = np.int16
        elif sampwidth == 4:
            dtype = np.int32
        else:
            dtype = np.int16

        audio = np.frombuffer(raw_data, dtype=dtype).astype(np.float32)
        if n_channels > 1:
            audio = audio.reshape(-1, n_channels).mean(axis=1)

        total_sec = len(audio) / framerate if framerate > 0 else 0
        if total_sec <= 0:
            return {"buckets": [], "silence_gaps": [], "peak_moments": []}

        # Normalize audio amplitude to [-1.0, 1.0]
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            audio = audio / max_val

        # 1. Energy per bucket (e.g., 30s)
        samples_per_bucket = int(bucket_sec * framerate)
        buckets = []
        for i in range(0, len(audio), samples_per_bucket):
            chunk = audio[i : i + samples_per_bucket]
            rms = np.sqrt(np.mean(chunk**2)) if len(chunk) > 0 else 0.0
            start_sec = round(i / framerate, 1)
            end_sec = round(min(len(audio), i + samples_per_bucket) / framerate, 1)
            buckets.append({
                "start": start_sec,
                "end": end_sec,
                "rms": round(float(rms), 4),
            })

        # 2. Silence gaps (pauses >= 0.8s with very low RMS)
        window_size = int(0.2 * framerate) # 200ms
        hop_size = int(0.1 * framerate)    # 100ms
        silence_thresh = 0.02
        
        silence_gaps = []
        in_silence = False
        silence_start = 0.0
        
        for idx in range(0, len(audio) - window_size, hop_size):
            win = audio[idx : idx + window_size]
            win_rms = np.sqrt(np.mean(win**2))
            t = idx / framerate
            if win_rms < silence_thresh:
                if not in_silence:
                    in_silence = True
                    silence_start = t
            else:
                if in_silence:
                    in_silence = False
                    if (t - silence_start) >= 0.8:
                        silence_gaps.append({
                            "start": round(silence_start, 2),
                            "end": round(t, 2),
                            "duration": round(t - silence_start, 2),
                        })

        # 3. Peak energy moments (top 10% highest RMS 5-second windows)
        win_5s = int(5.0 * framerate)
        peak_candidates = []
        for idx in range(0, len(audio) - win_5s, int(2.5 * framerate)):
            win = audio[idx : idx + win_5s]
            win_rms = np.sqrt(np.mean(win**2))
            peak_candidates.append({
                "time": round(idx / framerate, 1),
                "energy": round(float(win_rms), 4),
            })
        
        peak_candidates.sort(key=lambda x: x["energy"], reverse=True)
        peak_moments = peak_candidates[:10]
        peak_moments.sort(key=lambda x: x["time"])

        return {
            "total_duration": round(total_sec, 2),
            "buckets": buckets,
            "silence_gaps": silence_gaps,
            "peak_moments": peak_moments,
        }
    except Exception as e:
        logger.warning(f"Audio energy analysis failed: {e}")
        return {"buckets": [], "silence_gaps": [], "peak_moments": []}
