"""Audio input, VAD and Wakeword detection for Coglet.

Handles the ReSpeaker microphone input via sounddevice, performs Voice Activity 
Detection (WebRTC), and Wakeword detection (OpenWakeWord).
"""

import collections
import io
import logging
import math
import os
import queue
import time
from math import gcd
from typing import Any, Optional

import numpy as np
import sounddevice as sd
import webrtcvad
from openwakeword import Model as _OWWModel
from scipy.signal import resample_poly

# Attempt to import the shared logger; fallback if running standalone
try:
    from logging_setup import get_logger
    logger = get_logger()
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("audio")

# Global switch for Half-Duplex muting (controlled by coglet-pi.py)
_global_listen_enabled = True


def set_global_listen_state(enabled: bool) -> None:
    """Enable or disable audio recording globally (e.g. during TTS output)."""
    global _global_listen_enabled
    _global_listen_enabled = enabled


def _parse_device_env(v: Any, default: Any) -> Any:
    """Parse ALSA device index or name string from environment."""
    if v is None:
        return default
    s = str(v).strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        return s


class Recorder:
    """Audio recorder using sounddevice.RawInputStream.
    
    Features:
    - Thread-safe queue for raw int16 bytes
    - Optional software gain (AGC placeholder)
    - Global and local mute switches (for half-duplex operation)
    """

    def __init__(self, sr: int = 16000, vad_aggr: int = 2):
        self.sr = int(sr)
        self.vad_aggr = int(vad_aggr)

        # MIC environment
        self.device = _parse_device_env(os.getenv("MIC_DEVICE"), 0)
        self.channels = int(os.getenv("MIC_CHANNELS", "1"))
        self.gain_db = float(os.getenv("MIC_GAIN_DB", "0"))
        self.auto_gain = os.getenv("MIC_AUTO_GAIN", "0").lower() in ("1", "true", "yes", "on")
        self.target_dbfs = float(os.getenv("MIC_TARGET_DBFS", "-18"))
        self.max_gain_db = float(os.getenv("MIC_MAX_GAIN_DB", "35"))

        # Software gain (only for read() -> float32)
        self._lin_gain = float(10.0 ** (self.gain_db / 20.0)) if self.gain_db else 1.0

        # Buffers
        self._q: queue.Queue[bytes] = queue.Queue()
        self._resid = b""
        self._level_buf = np.empty(0, dtype=np.float32)
        self._level_max_sec = 2.0

        self._stream: Optional[sd.RawInputStream] = None
        self._running = False
        
        # Instance-wide mute flag
        self._listen = True

        logger.info(
            "[audio] MIC setup: device=%s sr=%s gain=%.1fdB agc=%s",
            self.device, self.sr, self.gain_db, self.auto_gain
        )

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("[audio] stream status: %s", status)

        # Mute check: must be enabled locally AND globally
        if not (self._listen and _global_listen_enabled):
            return

        if self.channels > 1:
            data_mono = indata[:, 0] 
            data_mono = np.ascontiguousarray(data_mono)
            raw_bytes = bytes(data_mono)
            x = data_mono.astype(np.float32) / 32768.0
        else:
            raw_bytes = bytes(indata)
            x = np.frombuffer(indata, dtype="<i2").astype(np.float32) / 32768.0

        # Raw int16 bytes
        self._q.put(bytes(indata))

        # Level metering (float32 conversion)
        x = np.frombuffer(indata, dtype="<i2").astype(np.float32) / 32768.0
        max_len = int(self._level_max_sec * self.sr)
        
        if self._level_buf.size == 0:
            self._level_buf = x
        else:
            # Append and trim ring buffer logic could be optimized, but keeps level history
            new_size = self._level_buf.size + x.size
            if new_size > max_len:
                trim = new_size - max_len
                self._level_buf = self._level_buf[trim:]
            self._level_buf = np.concatenate((self._level_buf, x))

    def start(self):
        if self._running:
            return
        try:
            self._stream = sd.RawInputStream(
                samplerate=self.sr,
                channels=self.channels,
                dtype="int16",
                device=self.device,
                callback=self._callback,
                blocksize=0  # auto
            )
            self._stream.start()
            self._running = True
            time.sleep(0.2)  # warm up
            lvl = self.mic_level_dbfs()
            logger.info("[audio] Stream started. Level ≈ %.1f dBFS", lvl if lvl else -99)
        except Exception as e:
            logger.error("[audio] Failed to start stream: %s", e)
            raise

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
        self._stream = None
        self._running = False
        self.flush()

    def flush(self):
        """Clear all buffered audio."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._resid = b""
        self._level_buf = np.empty(0, dtype=np.float32)

    def flush_input_buffers(self):
        self.flush()

    def set_listen(self, active: bool):
        self._listen = active

    def mic_level_dbfs(self) -> Optional[float]:
        if self._level_buf.size == 0:
            return None
        x = self._level_buf * self._lin_gain
        rms = float(np.sqrt(np.mean(np.square(x)) + 1e-12))
        return 20.0 * np.log10(rms + 1e-12)

    def get_queue_size(self) -> int:
        """Returns the number of items in the queue (approximate)."""
        return self._q.qsize()

    def read(self, n_samples: int) -> np.ndarray:
        """Read n_samples as float32 [-1..1] (for WakeWord)."""
        need_bytes = int(n_samples) * 2
        data = bytearray()

        if self._resid:
            data.extend(self._resid)
            self._resid = b""

        while len(data) < need_bytes:
            data.extend(self._q.get())

        if len(data) > need_bytes:
            self._resid = bytes(data[need_bytes:])
            data = data[:need_bytes]

        x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if self._lin_gain != 1.0:
            np.clip(x * self._lin_gain, -1.0, 1.0, out=x)
        return x

    def read_bytes(self, n_bytes: int) -> bytes:
        """Read exact number of raw bytes (for STT/VAD)."""
        data = bytearray()
        if self._resid:
            take = min(len(self._resid), n_bytes)
            data.extend(self._resid[:take])
            self._resid = self._resid[take:]

        while len(data) < n_bytes:
            data.extend(self._q.get())

        if len(data) > n_bytes:
            self._resid = bytes(data[n_bytes:])
            data = data[:n_bytes]
        return bytes(data)


class SpeechEndpoint:
    """Endpointing using WebRTC VAD and heuristic timing (guard/hang)."""

    def __init__(self, sr: int, vad_aggr: int = 2):
        self.sr = int(sr)
        self.vad = webrtcvad.Vad(int(vad_aggr))

        self.frame_ms = int(os.getenv("VAD_FRAME_MS", "30"))
        if self.frame_ms not in (10, 20, 30):
            self.frame_ms = 30
        
        self.start_win = int(os.getenv("VAD_START_WIN", "5"))
        self.start_min = int(os.getenv("VAD_START_MIN", "3"))
        self.start_consec = int(os.getenv("VAD_START_CONSEC_MIN", "3"))
        
        # Tuning parameters (updated defaults for better responsiveness)
        self.end_hang_ms = int(os.getenv("VAD_END_HANG_MS", "300"))
        self.end_guard_ms = int(os.getenv("VAD_END_GUARD_MS", "800"))
        self.preroll_ms = int(os.getenv("VAD_PREROLL_MS", "240"))
        self.max_utter = float(os.getenv("MAX_UTTER_S", "8.0"))
        self.no_speech_timeout = float(os.getenv("NO_SPEECH_TIMEOUT_S", "3.0"))

        self.frame_samples = (self.sr * self.frame_ms) // 1000
        self.frame_bytes = self.frame_samples * 2
        self.hang_frames = max(1, math.ceil(self.end_hang_ms / self.frame_ms))
        self.preroll_frames = max(0, self.preroll_ms // self.frame_ms)
        self.end_guard_s = self.end_guard_ms / 1000.0

    def record(self, recorder: Recorder, no_speech_timeout_s: Optional[float] = None) -> tuple[bytes, float]:
        """Record until silence is detected or timeout reached."""
        timeout = float(no_speech_timeout_s) if no_speech_timeout_s is not None else self.no_speech_timeout
        
        votes = collections.deque(maxlen=self.start_win)
        preroll = collections.deque(maxlen=self.preroll_frames)
        buf = io.BytesIO()

        start_ts = time.monotonic()
        speech_started = False
        started_at = 0.0
        frames_since_speech = 0
        consec_speech = 0

        while True:
            now = time.monotonic()
            limit = self.max_utter if speech_started else timeout
            if (now - start_ts) > limit:
                break

            frame = recorder.read_bytes(self.frame_bytes)
            if not frame: 
                continue

            try:
                is_speech = self.vad.is_speech(frame, self.sr)
            except Exception:
                is_speech = False

            if not speech_started:
                votes.append(1 if is_speech else 0)
                if self.preroll_frames > 0:
                    preroll.append(frame)
                
                if is_speech:
                    consec_speech += 1
                else:
                    consec_speech = 0

                # Trigger condition
                if len(votes) == self.start_win and sum(votes) >= self.start_min and consec_speech >= self.start_consec:
                    # Flush preroll
                    for prev_frame in preroll:
                        buf.write(prev_frame)
                    buf.write(frame)
                    speech_started = True
                    started_at = now
                    frames_since_speech = 0
            else:
                # Recording state
                buf.write(frame)
                if is_speech:
                    frames_since_speech = 0
                else:
                    frames_since_speech += 1
                    # End condition: enough silence AND minimum duration reached
                    if frames_since_speech >= self.hang_frames and (now - started_at) >= self.end_guard_s:
                        break

        data = buf.getvalue()
        duration = len(data) / (2.0 * self.sr)
        return data, duration


class Wakeword:
    """OpenWakeWord detector with refractory period and TTS suppression."""

    def __init__(self, backend: str, model_path: str, threshold: float, hw_sr: int):
        self.hw_sr = int(hw_sr)
        self.oww_sr = 16000
        self.threshold = float(threshold)
        
        # Resampler ratios
        g = gcd(self.hw_sr, self.oww_sr)
        self._up = self.oww_sr // g
        self._down = self.hw_sr // g

        # Buffer sizing (1280 samples @ 16k = 80ms)
        M = 1280
        win_ms = int(os.getenv("OWW_WIN_MS", "800"))
        hop_ms = int(os.getenv("OWW_HOP_MS", "160"))
        self.win_oww = max(M, (self.oww_sr * win_ms // 1000) // M * M)
        self.hop_oww = max(M, (self.oww_sr * hop_ms // 1000) // M * M)
        
        # Calculate how many hardware samples we need to read to produce one OWW hop
        self.hop_hw = int(self.hop_oww * self._down / self._up)

        self.rearm_ratio = float(os.getenv("WAKE_REARM_RATIO", "0.6"))
        self.min_gap_s = float(os.getenv("WAKE_MIN_GAP_S", "1.5"))
        self.rearm_low_n = int(os.getenv("WAKE_REARM_LOW_COUNT", "3"))
        self.after_tts_s = float(os.getenv("OWW_SUPPRESS_AFTER_TTS_S", "0.8"))

        self.last_wake_ts = 0.0
        self.suppress_until = 0.0
        self.armed = True
        self._below_consec = 0
        self._was_above = False
        
        self.ring = np.zeros(self.win_oww, dtype=np.float32)

        logger.info("[oww] Loading model %s (thr=%.2f)", os.path.basename(model_path), threshold)
        self.detector = _OWWModel(wakeword_models=[model_path], inference_framework="onnx")
        
        # Determine the model key (usually the filename)
        probe = np.zeros(self.win_oww, dtype=np.int16)
        res = self.detector.predict(probe)
        self.key = list(res.keys())[0]

    def reset_after_tts(self):
        """Suppress detection briefly after TTS to avoid self-triggering."""
        self.reset()
        self.armed = False
        self.suppress_until = time.monotonic() + self.after_tts_s

    def reset(self):
        """Resets the internal buffer and state of the WakeWord engine."""
        self.ring.fill(0.0)
        self._below_consec = 0
        self._was_above = False
        self.armed = True
        self.suppress_until = 0.0
        # Reset OpenWakeWord model state if supported
        if hasattr(self.detector, "reset") and callable(self.detector.reset):
            try:
                self.detector.reset()
            except Exception:
                pass

    def check_once(self, recorder: Recorder) -> bool:
        """Liest einen Chunk und prüft auf Wakeword (Non-blocking für Barge-In)."""
        # Wir lesen nur so viel, wie für einen OWW-Hop nötig ist
        chunk = recorder.read(self.hop_hw)
        
        # Resample & Ringbuffer Update (wie in wait)
        y = self._resample(chunk)
        self.ring = np.roll(self.ring, -y.size)
        self.ring[-y.size:] = y

        # Predict
        score = self._predict(self.ring)
        # check wakewaord detection quality
        # print(f"DEBUG Score: {score:.4f}", end="\r", flush=True)
        now = time.monotonic()

        if score >= self.threshold:
            self.last_wake_ts = now
            return True
        return False

    def wait(self, recorder: Recorder) -> None:
        """Block until wakeword is detected."""
        # Prime buffer
        needed = self.hop_hw * 4
        priming = np.empty(0, dtype=np.float32)
        while priming.size < needed:
            chunk = recorder.read(self.hop_hw)
            priming = np.concatenate((priming, chunk))
        
        resampled = self._resample(priming)
        if resampled.size >= self.win_oww:
            self.ring[:] = resampled[-self.win_oww:]
        else:
            self.ring[-resampled.size:] = resampled

        # Loop
        while True:
            chunk = recorder.read(self.hop_hw)
            y = self._resample(chunk)
            
            # Update ring buffer
            self.ring = np.roll(self.ring, -y.size)
            self.ring[-y.size:] = y

            # Predict
            score = self._predict(self.ring)
            now = time.monotonic()

            # Logic: Arming & Suppression
            if score < (self.threshold * self.rearm_ratio):
                self._below_consec += 1
            else:
                self._below_consec = 0

            if now < self.suppress_until:
                self._was_above = (score >= self.threshold)
                continue

            if not self.armed:
                if self._below_consec >= self.rearm_low_n:
                    self.armed = True
                self._was_above = (score >= self.threshold)
                if not self.armed:
                    continue

            if score >= self.threshold and not self._was_above:
                # WAKE
                self.last_wake_ts = now
                self.armed = False
                self.suppress_until = now + self.min_gap_s
                self._was_above = True
                return
            
            self._was_above = (score >= self.threshold)

    def _resample(self, x: np.ndarray) -> np.ndarray:
        if x.size == 0: return x
        return resample_poly(x, self._up, self._down).astype(np.float32)

    def _predict(self, buffer_f32: np.ndarray) -> float:
        # Round down to 80ms multiple
        n = (buffer_f32.size // 1280) * 1280
        if n == 0: return 0.0
        
        # Float -> Int16
        pcm = (np.clip(buffer_f32[:n], -1.0, 1.0) * 32767.0).astype(np.int16)
        
        # Inference
        out = self.detector.predict(pcm)
        return float(out.get(self.key, 0.0))
