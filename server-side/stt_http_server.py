#!/usr/bin/env python3
# stt_http_server.py
# Faster-Whisper + Flask: simple STT HTTP server for Coglet
# Optimized for In-Memory processing (no disk writes for audio upload)
# Updated: Dynamic Initial Prompt & Configurable Default Language

import os
import re
import time
import io
import logging
from typing import Dict, Any

from flask import Flask, request, jsonify
from faster_whisper import WhisperModel

# ----------------------------
# Logging Configuration
# ----------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("stt")

# ----------------------------
# Configuration (via ENV)
# ----------------------------
def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "on")

MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")              # cuda|cpu|auto
COMPUTE = os.getenv("WHISPER_COMPUTE", "float16")         # float16|int8_float16|int8
PORT = int(os.getenv("STT_HTTP_PORT", "5005"))

# NEW: Configurable Default Language (fallback if client sends nothing)
DEFAULT_LANG = os.getenv("STT_DEFAULT_LANG", "en").lower().strip()

# NEW: Language specific prompts to guide Whisper correctly
# Can be overridden via env vars in your service file
PROMPTS = {
    "de": os.getenv("WHISPER_PROMPT_DE", "Coglet ist der Name. Antworte auf Deutsch."),
    "en": os.getenv("WHISPER_PROMPT_EN", "Coglet is the name. Please answer in English.")
}
# Fallback prompt if language is totally unknown
DEFAULT_PROMPT = os.getenv("WHISPER_INITIAL_PROMPT", "Coglet.")

DOWNLOAD_ROOT = os.getenv("WHISPER_DOWNLOAD_ROOT", "")    # optional: e.g. /opt/coglet-stt/models

# Latency/Stability tuning
VAD_MIN_SIL_MS = int(os.getenv("WHISPER_VAD_MIN_SIL_MS", "300"))
BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "1"))      # 1 = greedy, fast
WORD_TIMESTAMPS = _env_bool("WHISPER_WORD_TIMESTAMPS", False)
COND_PREV = _env_bool("WHISPER_CONDITION_ON_PREV", False) # typically False for single requests

# Remove wakeword / false positives at sentence start (e.g. "Koglet:")
_WAKEWORD_RE = re.compile(r'^\s*(?:co?glet|koglet|cogled|kogled)\s*[:,\-\–—]?\s*', re.IGNORECASE)


def _normalize_text(t: str) -> str:
    """Light post-processing: remove wakeword and trim."""
    t = (t or "").strip()
    t = _WAKEWORD_RE.sub("", t)
    return t.strip()


def _model_init_kwargs() -> Dict[str, Any]:
    kw = dict(device=DEVICE, compute_type=COMPUTE)
    if DOWNLOAD_ROOT:
        kw["download_root"] = DOWNLOAD_ROOT
    return kw


# ----------------------------
# Initialize app and model
# ----------------------------
app = Flask(__name__)

# Load model once at startup
logger.info(
    "Starting with MODEL=%s DEVICE=%s COMPUTE=%s PORT=%d",
    MODEL, DEVICE, COMPUTE, PORT
)
logger.info(
    "Config: VAD=%dms BEAM=%d COND_PREV=%s WORD_TS=%s DEFAULT_LANG=%s",
    VAD_MIN_SIL_MS, BEAM_SIZE, COND_PREV, WORD_TIMESTAMPS, DEFAULT_LANG
)
logger.info("Prompts loaded: DE='%s' EN='%s'", PROMPTS['de'], PROMPTS['en'])

try:
    model = WhisperModel(MODEL, **_model_init_kwargs())
except Exception as e:
    logger.critical("Failed to load model: %s", e)
    raise e


# ----------------------------
# Routes
# ----------------------------
@app.get("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "model": MODEL,
        "device": DEVICE,
        "compute": COMPUTE,
        "port": PORT,
        "default_lang": DEFAULT_LANG,
        "prompts": PROMPTS,
        "vad_min_sil_ms": VAD_MIN_SIL_MS,
        "beam_size": BEAM_SIZE,
        "condition_on_previous_text": COND_PREV,
        "word_timestamps": WORD_TIMESTAMPS,
        "download_root": DOWNLOAD_ROOT or None,
        "version": "1.3.1-multilang-env"
    })


@app.post("/stt")
def stt():
    try:
        if "audio" not in request.files:
            # FIX: Updated help text to be less confusing
            return jsonify(error="send multipart/form-data with: audio=@file.wav [lang=de|en]"), 400

        # 1. Try 'lang' from POST data
        # 2. Try 'lang' from URL params
        # 3. Fallback to configured DEFAULT_LANG (from ENV)
        lang = request.form.get("lang") or request.args.get("lang") or DEFAULT_LANG
        lang = lang.lower().strip()

        # Select correct initial prompt
        # Fallback to DEFAULT_PROMPT only if language is neither de nor en
        current_prompt = PROMPTS.get(lang, DEFAULT_PROMPT)

        f = request.files["audio"]
        t0 = time.time()

        # --- In-Memory Processing Start ---
        audio_data = f.read()
        if not audio_data:
            return jsonify(error="Empty audio file"), 400
            
        audio_stream = io.BytesIO(audio_data)
        # --- In-Memory Processing End ---

        segments, info = model.transcribe(
            audio_stream,
            language=lang,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=VAD_MIN_SIL_MS),
            beam_size=BEAM_SIZE,
            word_timestamps=WORD_TIMESTAMPS,
            initial_prompt=current_prompt,
            condition_on_previous_text=COND_PREV
        )

        text = "".join(seg.text for seg in segments).strip()
        text = _normalize_text(text)
        dt_ms = int((time.time() - t0) * 1000)

        logger.debug("processed [%s] in %dms: %s...", lang, dt_ms, text[:50])

        return jsonify(text=text, language=info.language, time_ms=dt_ms)

    except Exception:
        app.logger.exception("Unhandled exception in /transcribe")
        return jsonify(error="Internal server error"), 500

# ----------------------------
# Main
# ----------------------------
if __name__ == "__main__":
    logger.info("Server listening on 0.0.0.0:%d", PORT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
