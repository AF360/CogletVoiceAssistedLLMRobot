#!/usr/bin/env python3
# stt_http_server.py
# Faster-Whisper + Flask: simple STT HTTP server for Coglet
# Endpoints:
#   GET  /healthz
#   POST /stt        (multipart/form-data: audio=@file.wav [, lang=de] )

import os
import re
import time
import tempfile
import traceback
from typing import Dict, Any

from flask import Flask, request, jsonify
from faster_whisper import WhisperModel

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

INITIAL_PROMPT = os.getenv("WHISPER_INITIAL_PROMPT", "Coglet is the name. Reply in englisch.")
DOWNLOAD_ROOT = os.getenv("WHISPER_DOWNLOAD_ROOT", "")    # optional: e.g. /opt/coglet-stt/models

# Latency/Stability tuning
VAD_MIN_SIL_MS = int(os.getenv("WHISPER_VAD_MIN_SIL_MS", "300"))
BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "1"))      # 1 = greedy, fast
WORD_TIMESTAMPS = _env_bool("WHISPER_WORD_TIMESTAMPS", False)
COND_PREV = _env_bool("WHISPER_CONDITION_ON_PREV", False) # typically False for single requests

# Optional simple logs
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

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
model = WhisperModel(MODEL, **_model_init_kwargs())

if LOG_LEVEL in ("INFO", "DEBUG"):
    print(f"[stt] Starting with MODEL={MODEL} DEVICE={DEVICE} COMPUTE={COMPUTE} "
          f"PORT={PORT} VAD_MIN_SIL_MS={VAD_MIN_SIL_MS} BEAM_SIZE={BEAM_SIZE} "
          f"COND_PREV={COND_PREV} WORD_TS={WORD_TIMESTAMPS} DOWNLOAD_ROOT={DOWNLOAD_ROOT or '-'}")


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
        "initial_prompt_set": bool(INITIAL_PROMPT),
        "vad_min_sil_ms": VAD_MIN_SIL_MS,
        "beam_size": BEAM_SIZE,
        "condition_on_previous_text": COND_PREV,
        "word_timestamps": WORD_TIMESTAMPS,
        "download_root": DOWNLOAD_ROOT or None,
        "version": "1.2.0"
    })


@app.post("/stt")
def stt():
    try:
        if "audio" not in request.files:
            return jsonify(error="send multipart/form-data with: audio=@file.wav [lang=de]"), 400

        lang = request.form.get("lang") or request.args.get("lang") or "de"
        f = request.files["audio"]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            f.save(tmp.name)
            t0 = time.time()

            segments, info = model.transcribe(
                tmp.name,
                language=lang,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=VAD_MIN_SIL_MS),
                beam_size=BEAM_SIZE,                      # greedy for low latency
                word_timestamps=WORD_TIMESTAMPS,
                initial_prompt=INITIAL_PROMPT or None,
                condition_on_previous_text=COND_PREV
            )

            text = "".join(seg.text for seg in segments).strip()
            text = _normalize_text(text)
            dt_ms = int((time.time() - t0) * 1000)

        return jsonify(text=text, language=info.language, time_ms=dt_ms)

    except Exception as e:
        # Return errors as JSON and log the stack trace
        traceback.print_exc()
        return jsonify(error=str(e)), 500


# ----------------------------
# Main
# ----------------------------
if __name__ == "__main__":
    # Flask dev server is fine for LAN; use gunicorn for multiple clients.
    app.run(host="0.0.0.0", port=PORT, threaded=True)

