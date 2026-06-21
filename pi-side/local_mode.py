#!/usr/bin/env python3
"""
Coglet Pi side:
- Wakeword (openwakeword) -> recording (sounddevice + webrtcvad)
- Local pipeline: Pi sends recorded audio to the STT server, calls Ollama directly,
  and speaks replies through local Piper/MQTT TTS.
- Half-duplex: mic is muted during TTS playback.
- Deep Sleep: Enters low power/servo mode after inactivity (no voice interaction).
- ReSpeaker Hardware VAD & DOA Integration (xvf_mic).
- Follow-Up Logic.
- Barge-In self-interruption (flush).
- Body turning no longer blocks wakeword detection.
- Full Localization support (DE/EN) for Text & STT via COGLET_LANG
- Email feature uses direct Ollama call with a dedicated drafting prompt.

ENV (see /etc/default/coglet-pi):
  STT_URL       (http://<server>:5005)
  OLLAMA_URL    (http://<server>:11434)
  MIC_SR, MIC_DEVICE
  WAKEWORD_BACKEND, OWW_MODEL, OWW_THRESHOLD
  PIPER_FIFO    (Piper fallback / FIFO TTS path)
  TTS_WPM (e.g., 185), TTS_PUNCT_PAUSE_MS (e.g., 180)
  COGLET_LANG (de/en)
"""

__version__ = "1.1.2"

import os
import sys
import io
import json
import random
import time
import threading
import collections
import subprocess
import re
import math
import wave
import inspect
import importlib
import logging
import signal
from dataclasses import dataclass
import numpy as np
import requests
import uuid
import sounddevice as sd
import paho.mqtt.client as mqtt
import errno
import stat
from contextlib import contextmanager
from typing import Any, Callable, Dict, Mapping, Optional, Set
from scipy.signal import resample_poly

from startup_checks import (
    StartupCheckError,
    check_ollama_model,
    check_piper_mqtt_connectivity,
    check_stt_health,
)


try:
    import email_sender
except ImportError:
    email_sender = None


from hardware.audio import Recorder, SpeechEndpoint, Wakeword, set_global_listen_state
from command_utils import normalize_command_text as _normalize_command_text
from robot_runtime import (CogletState, ReSpeakerMic, XVF_MIC_AVAILABLE as _XVF_MIC_AVAILABLE, anim_error, anim_listen_start, anim_listen_stop, anim_talk_start, anim_talk_stop, anim_think_start, anim_think_stop, apply_personality_neutral_pose as _apply_personality_neutral_pose, cleanup_servo_hardware as _cleanup_servo_hardware, demomode, eyelids_set_mode as _eyelids_set_mode, initialize_all_servos as _initialize_all_servos, initialize_status_led as _initialize_status_led, led_set_state_safe as _led_set_state_safe, restore_neutral_pose_and_close_lid as _restore_neutral_pose_and_close_lid, set_deep_sleep_led_pulse, setup_face_tracking as _setup_face_tracking, start_idle_animation as _start_idle_animation, stop_idle_animation as _stop_idle_animation)


from logging_setup import get_logger, setup_logging

setup_logging()
logger = get_logger()


LANG_CODE = os.getenv("COGLET_LANG", "de").lower().strip()


_STRINGS = {
    "de": {

        "stt_lang": "de",
        "piper_voice": "/opt/piper/voices/de_DE-thorsten-high.onnx",
        "piper_json": "/opt/piper/voices/de_DE-thorsten-high.onnx.json",


        "model_ready": "Alle Subsysteme hochgefahren. Ich bin bereit zu helfen.",
        "model_confirm": "Ja?",
        "model_byebye": "Tschüssen!",
        "ack_eoc": "Alles klar.",
        "ack_ams": "OK.",
        "ack_ds": "Ich mache ein Nickerchen. Wecke mich, wenn Du etwas von mir brauchst .",


        "email_missing_recipient": "Keine Empfängeradresse konfiguriert.",
        "email_success": "Alles klar, ich habe dir eine ausführliche E-Mail geschickt.",
        "email_error": "Ich konnte die E-Mail leider nicht senden.",
        "email_subject_fallback": "Info von Coglet",
        "email_sys_prompt": (
            "Du bist ein professioneller, freundlicher Redakteur. "
            "Deine Aufgabe ist es, ausführliche, hilfreiche und schön formatierte E-Mails zu schreiben. "
            "Nutze HTML zur Strukturierung: <h2> für Überschriften, <ul>/<li> für Listen, <b> für Wichtiges und <p> für Absätze. "
            "Nutze Emojis 🌟, wo es passend ist. "
            "Antworte NICHT kurz, sondern detailliert und vollständig."
        ),
        "email_user_prompt_template": (
            "Benutzeranfrage: '{user_text}'.\n\n"
            "Generiere eine E-Mail mit:\n"
            "1. Einem passenden Betreff (Subject: ...)\n"
            "2. Einem Trenner (---)\n"
            "3. Dem ausführlichen HTML-Inhalt (ohne <html>/<body> Tags, nur der Content).\n"
            "Beispiel-Format:\n"
            "Subject: Leckeres Rezept für Dich 🥗\n"
            "---\n"
            "<h2>Hier ist dein Rezept</h2><p>...</p>"
        )
    },
    "en": {

        "stt_lang": "en",
        "piper_voice": "/opt/piper/voices/en_US-lessac-high.onnx",
        "piper_json": "/opt/piper/voices/en_US-lessac-high.onnx.json",


        "model_ready": "All subsystems up and running. Ready to help.",
        "model_confirm": "Yes?",
        "model_byebye": "Goodbye!",
        "ack_eoc": "Alright.",
        "ack_ams": "OK.",
        "ack_ds": "Taking a nap. Wake me when you need me.",


        "email_missing_recipient": "No recipient address configured.",
        "email_success": "Alright, I sent you a detailed email and am now waiting for the wakeword.",
        "email_error": "Unfortunately, I could not send the email.",
        "email_subject_fallback": "Info from Coglet",
        "email_sys_prompt": (
            "You are a professional, friendly editor. "
            "Your task is to write detailed, helpful, and beautifully formatted emails. "
            "Use HTML for structure: <h2> for headers, <ul>/<li> for lists, <b> for emphasis, and <p> for paragraphs. "
            "Use emojis 🌟 where appropriate. "
            "Do NOT be brief; be detailed and complete."
        ),
        "email_user_prompt_template": (
            "User request: '{user_text}'.\n\n"
            "Generate an email with:\n"
            "1. A fitting Subject line (Subject: ...)\n"
            "2. A separator (---)\n"
            "3. The detailed HTML content (no <html>/<body> tags, just content).\n"
            "Example Format:\n"
            "Subject: Delicious Recipe for You 🥗\n"
            "---\n"
            "<h2>Here is your recipe</h2><p>...</p>"
        )
    }
}

def get_msg(key: str, env_var: Optional[str] = None) -> str:
    """Retrieve a localized string, optionally overridden by an env var."""

    lang_dict = _STRINGS.get(LANG_CODE, _STRINGS["en"])

    default_text = lang_dict.get(key, f"MISSING_STRING: {key}")


    if env_var:
        return os.getenv(env_var, default_text)
    return default_text


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}

DEMOMODE         = _parse_bool(os.getenv("DEMOMODE"), False)
BARGE_IN         = _parse_bool(os.getenv("BARGE_IN"), True)
TTS_MODE         = os.getenv("TTS_MODE", "mqtt")
STT_URL          = os.getenv("STT_URL", "http://192.168.10.161:5005")
OLLAMA_URL       = os.getenv("OLLAMA_URL", "http://192.168.10.161:11434")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "coglet:latest")
LLM_KEEP_ALIVE   = os.getenv("LLM_KEEP_ALIVE", "30m")


MODEL_CONFIRM    = get_msg("model_confirm", "MODEL_CONFIRM")
MODEL_READY      = get_msg("model_ready", "MODEL_READY")
MODEL_BYEBYE     = get_msg("model_byebye", "MODEL_BYEBYE")
EOC_ACK          = get_msg("ack_eoc", "EOC_ACK")
AMS_ACK          = get_msg("ack_ams", "AMS_ACK")
DS_ACK           = get_msg("ack_ds", "DS_ACK")


PIPER_VOICE      = get_msg("piper_voice", "PIPER_VOICE")
PIPER_VOICE_JSON = get_msg("piper_json", "PIPER_VOICE_JSON")


if not os.getenv("STT_LANG"):
    os.environ["STT_LANG"] = get_msg("stt_lang")


OWW_MODEL        = os.getenv("OWW_MODEL", "/opt/coglet-pi/.venv/lib/python3.13/site-packages/openwakeword/resources/models/wheatley.onnx")
OWW_THRESHOLD    = float(os.getenv("OWW_THRESHOLD", "0.35"))
OWW_DEBUG        = int(os.getenv("OWW_DEBUG", "0"))
MIC_SR           = int(os.getenv("MIC_SR", "16000"))
VAD_AGGR         = int(os.getenv("VAD_AGGRESSIVENESS", "2"))
WAKEWORD_BACKEND = os.getenv("WAKEWORD_BACKEND", "oww")
WAKEWORD_BACKEND_MODE = WAKEWORD_BACKEND.strip().lower()
XVF_WAKE_BACKENDS = {"xvf_vad", "hardware_vad", "vad"}
XVF_WAKE_PREROLL_S = float(os.getenv("XVF_WAKE_PREROLL_S", "0.6"))
XVF_WAKE_HOLD_EYELIDS = _parse_bool(os.getenv("XVF_WAKE_HOLD_EYELIDS"), True)

PIPER_FIFO       = os.getenv("PIPER_FIFO", "/run/piper/in.jsonl")
PIPER_MQTT_HOST  = os.getenv("PIPER_MQTT_HOST", "127.0.0.1")
PIPER_MQTT_PORT  = int(os.getenv("PIPER_MQTT_PORT", "1883"))
PIPER_MQTT_USERNAME = os.getenv("PIPER_MQTT_USERNAME", "")
PIPER_MQTT_PASSWORD = os.getenv("PIPER_MQTT_PASSWORD", "")
PIPER_MQTT_CMD_QOS = max(0, min(2, int(os.getenv("PIPER_MQTT_CMD_QOS", os.getenv("PIPER_MQTT_QOS", "1")))))
PIPER_MQTT_STATUS_QOS = 0
PIPER_MQTT_TLS   = os.getenv("PIPER_MQTT_TLS", "0") in ("1","true","True")
PIPER_MQTT_FORCE_V311 = os.getenv("PIPER_MQTT_FORCE_V311", "0").lower() in {"1", "true", "yes", "on"}
if hasattr(mqtt, "MQTTv5") and not PIPER_MQTT_FORCE_V311:
    PIPER_MQTT_PROTOCOL = mqtt.MQTTv5
    PIPER_MQTT_IS_V5 = True
else:
    PIPER_MQTT_PROTOCOL = getattr(mqtt, "MQTTv311", 4)
    PIPER_MQTT_IS_V5 = False

try:
    _MQTT_CLIENT_SIGNATURE = inspect.signature(mqtt.Client.__init__)
except (AttributeError, TypeError, ValueError):
    _MQTT_CLIENT_SIGNATURE = None


def _mqtt_client_supports(parameter: str) -> bool:
    return bool(
        _MQTT_CLIENT_SIGNATURE and parameter in _MQTT_CLIENT_SIGNATURE.parameters
    )
MQTT_CANCEL_EXPIRY = max(1, int(float(os.getenv("MQTT_CANCEL_EXPIRY", "2.0"))))
MQTT_BASE        = os.getenv("MQTT_BASE", "coglet/tts")
TOPIC_SAY        = f"{MQTT_BASE}/say"
TOPIC_CANCEL     = f"{MQTT_BASE}/cancel"
TOPIC_STATUS     = f"{MQTT_BASE}/status"
TTS_WPM          = int(os.getenv("TTS_WPM", "185"))
TTS_PUNCT_MS     = int(os.getenv("TTS_PUNCT_PAUSE_MS", "180"))
SENTENCE_RE      = re.compile(r'[.!?…]\s($|\S)')

DEEP_SLEEP_TIMEOUT_S = float(os.getenv("DEEP_SLEEP_TIMEOUT_S", "300.0"))

_shutdown_event = threading.Event()

def _graceful_shutdown(signum=None, frame=None) -> None:
    if _shutdown_event.is_set():
        return
    _shutdown_event.set()
    try:
        _restore_neutral_pose_and_close_lid()
    except Exception as exc:
        logger.debug("Servo parking on shutdown failed: %s", exc)
    try:
        _led_set_state_safe(CogletState.OFF)
    except Exception as exc:
        logger.debug("Status LED shutdown failed: %s", exc)
    raise KeyboardInterrupt

_tts_active = False
sd.default.samplerate = MIC_SR


def _is_program_exit_command(text: str) -> bool:
    normalized = _normalize_command_text(text)
    if not normalized:
        return False
    collapsed = normalized.replace(" ", "")
    exits = {"programm ende", "programmende", "programm-ende"}
    return normalized in exits or collapsed in exits


def _is_email_request(text: str) -> bool:
    normalized = _normalize_command_text(text)


    if re.search(r"\b(per|als|via|by|as|in)\s+(an\s+|a\s+|an\s+)?(e-?mail|mail|nachricht|message)\b", normalized):
        logger.info(f"[intent] Detected Email intent (Preposition): {text}")
        return True


    if re.search(r"\b(e-?mail|mail)(e|en|st|t|s|ing|ed)?\s+(mir|uns|an|me|us|to)\b", normalized):
        logger.info(f"[intent] Detected Email intent (Verb): {text}")
        return True


    words = set(normalized.split())
    strong_verbs = {

        "schick", "schicke", "schicken", "schickt",
        "sende", "senden", "sendet",
        "versende", "versenden", "verschicken", "verschickt",
        "schreib", "schreibe", "erstell", "erstelle",

        "send", "sends", "sending", "sent",
        "write", "writing",
        "compose", "create",
        "forward", "dispatch"
    }
    objects = {

        "mail", "email", "e-mail", "e-mails", "emails", "nachricht",

        "message", "copy", "letter"
    }

    if not words.isdisjoint(strong_verbs) and not words.isdisjoint(objects):
        logger.info(f"[intent] Detected Email intent (Verb+Obj): {text}")
        return True

    return False

def _handle_email_request(user_text: str, rec, kw) -> bool:
    """
    Handles an email generation request via a one-shot LLM call.
    Uses a specific system prompt and increased token limit to ensure
    verbose and formatted email content.
    """
    if email_sender is None:
        logger.error("[email] Module not imported/available")
        return False

    email_to = os.getenv("EMAIL_TO")
    if not email_to:
        logger.warning("[email] EMAIL_TO not configured in environment")
        speak_and_back_to_idle(get_msg("email_missing_recipient"), rec, kw)
        return True

    logger.info("[email] Handling request: %r", user_text)


    system_prompt = get_msg("email_sys_prompt")


    user_prompt_template = get_msg("email_user_prompt_template")
    user_prompt = user_prompt_template.replace("{user_text}", user_text)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    anim_think_start()
    try:


        response = _ollama_chat(messages, temperature=0.6, num_predict=4096, timeout=600.0)
    except Exception as e:
        logger.error("[email] LLM generation error: %s", e)
        anim_think_stop()
        speak_and_back_to_idle("Fehler bei der Generierung.", rec, kw)
        return True

    anim_think_stop()


    subject = get_msg("email_subject_fallback")
    body = response

    if "Subject:" in response and "---" in response:
        try:
            parts = response.split("---", 1)
            header_part = parts[0].strip()
            body_part = parts[1].strip()


            m = re.search(r"Subject:\s*(.*)", header_part, re.IGNORECASE)
            if m:
                subject = m.group(1).strip()

            body = body_part
        except Exception as e:
            logger.warning("[email] Parsing failed, sending raw content: %s", e)


    if subject == get_msg("email_subject_fallback"):
        subject = f"{subject}: {user_text[:20]}..."


    try:
        email_sender.send_email_smtp(email_to, subject, body)
        logger.info("[email] Sent successfully to %s", email_to)
        speak_and_back_to_idle(get_msg("email_success"), rec, kw)
    except Exception as e:
        logger.error("[email] SMTP sending failed: %s", e)
        speak_and_back_to_idle(get_msg("email_error"), rec, kw)

    return True

def _fifo_write_nonblock(path: str, line: str) -> bool:
    try:
        st = os.stat(path)
        if not stat.S_ISFIFO(st.st_mode):
            logger.warning("[piper] %s exists but is not FIFO", path)
            return False
    except FileNotFoundError:
        return False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as e:
        if e.errno in (errno.ENXIO, errno.EWOULDBLOCK):
            logger.info("[piper] FIFO has no reader → skip FIFO path")
            return False
        logger.error("[piper] FIFO open error: %s", e)
        return False
    try:
        os.write(fd, line.encode("utf-8"))
        return True
    except OSError as e:
        logger.error("[piper] FIFO write error: %s", e)
        return False
    finally:
        try: os.close(fd)
        except Exception: pass


_mqtt_client = None
_mqtt_connected = False
_tts_events: Dict[str, threading.Event] = {}
_tts_states: Dict[str, str] = {}
_tts_estimates: Dict[str, float] = {}
_tts_manual_started: Set[str] = set()
_tts_anim_started: Set[str] = set()
_last_tts_id: str = ""

def _ensure_talk_anim_started(tts_id: str) -> None:
    if tts_id in _tts_anim_started:
        return
    _tts_anim_started.add(tts_id)
    anim_talk_start()

def _clear_tts_tracking(tts_id: str) -> None:
    _tts_manual_started.discard(tts_id)
    _tts_anim_started.discard(tts_id)
    _tts_estimates.pop(tts_id, None)

def _mqtt_on_connect(client, userdata, flags, rc, properties=None):
    global _mqtt_connected
    _mqtt_connected = (rc == 0)
    logger.info("[mqtt] connect rc=%s ok=%s", rc, _mqtt_connected)
    if _mqtt_connected:
        try:
            client.subscribe(TOPIC_STATUS, qos=PIPER_MQTT_STATUS_QOS)
            logger.info("[mqtt] subscribed %s", TOPIC_STATUS)
        except Exception as e:
            logger.error("[mqtt] subscribe error: %s", e)

def _handle_tts_state(tts_id: str, state: str, payload: Dict[str, Any]) -> None:
    prev = _tts_states.get(tts_id)
    if prev == state:
        return
    _tts_states[tts_id] = state

    if state == "START":
        if tts_id in _tts_manual_started:
            _tts_manual_started.discard(tts_id)
    elif state == "SPEAKING":
        if prev not in ("START", "SPEAKING"):
            if tts_id in _tts_manual_started:
                _tts_manual_started.discard(tts_id)
            _ensure_talk_anim_started(tts_id)
    elif state in ("DONE", "CANCELLED"):
        if tts_id in _tts_anim_started:
            anim_talk_stop()
        _clear_tts_tracking(tts_id)
    elif state == "ERROR":
        reason = payload.get("reason") if isinstance(payload.get("reason"), str) else ""
        if not reason:
            reason = payload.get("error") if isinstance(payload.get("error"), str) else ""
        _ensure_talk_anim_started(tts_id)
        anim_error(reason)
        anim_talk_stop()
        _clear_tts_tracking(tts_id)

def _mqtt_on_message(client, userdata, msg):
    try:
        if msg.topic != TOPIC_STATUS:
            return
        js = json.loads(msg.payload.decode("utf-8", errors="ignore") or "{}")
        tts_id = js.get("id") or ""
        state  = (js.get("state") or "").upper()
        if not tts_id:
            return
        _handle_tts_state(tts_id, state, js)
        ev = _tts_events.get(tts_id)
        if ev and state in ("DONE", "CANCELLED", "ERROR"):
            ev.set()
    except Exception as e:
        logger.error("[mqtt] on_message error: %s", e)

def _mqtt_connect():
    global _mqtt_client, _mqtt_connected
    if mqtt is None or not PIPER_MQTT_HOST:
        return False
    if _mqtt_client is None:
        client_kwargs: Dict[str, Any] = {
            "client_id": f"coglet-pi-{uuid.uuid4().hex[:8]}",
            "protocol": PIPER_MQTT_PROTOCOL,
        }
        if hasattr(mqtt, "CallbackAPIVersion"):
            client_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        supports_clean_start = _mqtt_client_supports("clean_start")
        supports_clean_session = _mqtt_client_supports("clean_session")
        clean_start_flag = getattr(mqtt, "MQTT_CLEAN_START_FIRST_ONLY", 1)
        if PIPER_MQTT_IS_V5:
            if supports_clean_start:
                client_kwargs["clean_start"] = clean_start_flag
        else:
            if supports_clean_session:
                client_kwargs["clean_session"] = True
            elif supports_clean_start:
                client_kwargs["clean_start"] = clean_start_flag
        _mqtt_client = mqtt.Client(**client_kwargs)
        if PIPER_MQTT_USERNAME or PIPER_MQTT_PASSWORD:
            _mqtt_client.username_pw_set(PIPER_MQTT_USERNAME or None, PIPER_MQTT_PASSWORD or None)
        if PIPER_MQTT_TLS:
            try:
                _mqtt_client.tls_set()
            except Exception as e:
                logger.error("[mqtt] tls_set error: %s", e)
        _mqtt_client.on_connect = _mqtt_on_connect
        _mqtt_client.on_message = _mqtt_on_message
        try:
            _mqtt_client.connect(PIPER_MQTT_HOST, PIPER_MQTT_PORT, keepalive=60)
            _mqtt_client.loop_start()
        except Exception as e:
            logger.error("[mqtt] connect error: %s", e)
            return False
    return True

def _piper_mqtt_publish(text: str, estimate_hint: Optional[float] = None) -> str:
    global _last_tts_id
    if not _mqtt_connect():
        return ""
    try:
        tts_id = uuid.uuid4().hex[:12]
        payload = {"id": tts_id, "text": text}
        if PIPER_VOICE:
            payload["voice"] = PIPER_VOICE
        ev = _tts_events.get(tts_id)
        if ev is None:
            ev = threading.Event()
            _tts_events[tts_id] = ev
        else:
            try: ev.clear()
            except Exception: pass
        if estimate_hint is not None:
            _tts_estimates[tts_id] = estimate_hint
        info = _mqtt_client.publish(
            TOPIC_SAY,
            json.dumps(payload, ensure_ascii=False),
            qos=PIPER_MQTT_CMD_QOS,
            retain=False,
        )
        info.wait_for_publish(timeout=2.0)
        if not info.is_published():
            logger.warning("[mqtt] publish not confirmed (timeout)")
            _tts_estimates.pop(tts_id, None)
            return ""
        logger.info("[mqtt] → %s id=%s (%d chars)", TOPIC_SAY, tts_id, len(text))
        _last_tts_id = tts_id
        return tts_id
    except Exception as e:
        logger.error("[mqtt] publish error: %s", e)
        return ""

def _wait_for_tts_done(tts_id: str, fallback_seconds: float = 0.0, hard_timeout: float = 30.0):
    if not tts_id:
        if fallback_seconds > 0:
            time.sleep(fallback_seconds)
        return
    ev = _tts_events.get(tts_id)
    if not ev:
        if fallback_seconds > 0:
            time.sleep(fallback_seconds)
        return
    try:
        waited = ev.wait(timeout=hard_timeout)
    except KeyboardInterrupt:
        return
    if not waited:
        logger.warning("[mqtt] status timeout for id=%s; using estimate %.2fs", tts_id, fallback_seconds)
        if fallback_seconds > 0:
            time.sleep(fallback_seconds)
    _tts_events.pop(tts_id, None)

@contextmanager
def half_duplex_tts():
    global _tts_active
    _tts_active = True
    try:
        if BARGE_IN:
            yield
        else:
            set_global_listen_state(False)
            try:
                yield
            finally:
                set_global_listen_state(True)
    finally:
        _tts_active = False

def _voice_sample_rate() -> int:
    try:
        with open(PIPER_VOICE_JSON, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return int(cfg.get("audio", {}).get("sample_rate", 22050))
    except Exception:
        return 22050

def estimate_tts_seconds(text: str) -> float:
    words = max(1, len(re.findall(r'\b\w+\b', text, flags=re.UNICODE)))
    base = words * (60.0 / max(60, TTS_WPM))
    pauses = text.count('.') + text.count('!') + text.count('?') + text.count('…')
    commas = text.count(',') + text.count(';') + text.count(':')
    extra = pauses * (TTS_PUNCT_MS/1000.0) + commas * (TTS_PUNCT_MS/2000.0)
    return base + extra + 0.2

def clean_tts_text(text: str) -> str:
    text = re.sub(r"\[(laugh|chuckle|giggle|smile|sigh)\]", "", text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()

def say(text: str, recorder: Optional[Recorder] = None, wakeword: Optional[Wakeword] = None):
    if not text or not text.strip():
        return
    payload = {"text": text}
    line = json.dumps(payload, ensure_ascii=False) + "\n"

    with half_duplex_tts():
        used = False
        if (TTS_MODE.lower() == "mqtt" or PIPER_MQTT_HOST) and (mqtt is not None):
            est = max(0.6, estimate_tts_seconds(text))
            tts_id = _piper_mqtt_publish(text, estimate_hint=est)

            if tts_id:
                used = True


                deadline = time.time() + 5.0

                while time.time() < deadline:
                    state = _tts_states.get(tts_id)


                    if state in {"SPEAKING", "DONE", "CANCELLED", "ERROR"}:
                        break
                    time.sleep(0.05)


                _ensure_talk_anim_started(tts_id)
                _tts_manual_started.add(tts_id)


                if BARGE_IN and recorder and wakeword:

                    _flush_input_buffers(recorder)
                    if hasattr(wakeword, "reset"):
                        wakeword.reset()


                    start_wait = time.time()
                    timeout_sec = max(6.0, est * 2 + 2.0)

                    while time.time() < start_wait + timeout_sec:
                        ev = _tts_events.get(tts_id)

                        if ev and ev.is_set():
                            break


                        if wakeword.check_once(recorder):
                            logger.info("[barge-in] WAKEWORD DETECTED! Cancelling TTS...")
                            payload_cancel = json.dumps({"id": tts_id, "text": "STOP"}, ensure_ascii=False)
                            if _mqtt_client:
                                _mqtt_client.publish(TOPIC_CANCEL, payload_cancel, qos=1)
                            anim_talk_stop()
                            break
                        time.sleep(0.02)

                    _tts_events.pop(tts_id, None)
                else:

                    _wait_for_tts_done(tts_id, fallback_seconds=est, hard_timeout=max(6.0, est * 2 + 2.0))

                time.sleep(0.1)
                if _tts_states.get(tts_id) not in {"DONE", "CANCELLED", "ERROR"}:
                    anim_talk_stop()
                _clear_tts_tracking(tts_id)

        if used:
            return


        anim_talk_start()
        if _fifo_write_nonblock(PIPER_FIFO, line):
            if not BARGE_IN:
                time.sleep(estimate_tts_seconds(text))
            anim_talk_stop()
            return
        anim_talk_stop()


        try:
            rate = str(_voice_sample_rate())
            cmd_piper = ["/opt/piper/piper", "--model", PIPER_VOICE, "--config", PIPER_VOICE_JSON,
                         "--output-raw", "--sentence_silence", "0.06"]
            cmd_play  = ["aplay", "-q", "-D", os.getenv("SPEAKER_DEVICE", "default"),
                         "-r", rate, "-f", "S16_LE", "-t", "raw", "-"]
            p1 = subprocess.Popen(cmd_piper, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p2 = subprocess.Popen(cmd_play, stdin=p1.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                p1.stdin.write((text + "\n").encode("utf-8"))
                p1.stdin.close()
            except BrokenPipeError:
                pass
            p2.wait(timeout=120)
            p1.wait(timeout=120)
        finally:
            try: anim_talk_stop()
            except Exception: pass

def chat_stream(prompt: str):
    url = f"{OLLAMA_URL}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "keep_alive": LLM_KEEP_ALIVE,
        "stream": True,
        "messages": [{"role":"user","content": prompt}],
        "options": {
            "num_predict": 140,
            "temperature": 0.6,
            "top_p": 0.9,
            "repeat_penalty": 1.15
        }
    }
    with requests.post(url, json=payload, stream=True, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line: continue
            try: j = json.loads(line)
            except json.JSONDecodeError: continue
            if j.get("done"): break
            chunk = j.get("message", {}).get("content", "")
            if chunk: yield chunk

def _flush_input_buffers(recorder):
    """Best-effort helper to purge pending audio frames.
    recorder.flush() is the direct, class-specific clear operation. Some
    implementations (like hardware.audio.Audio) also expose a dedicated
    flush_input_buffers() that may clear device-level buffers beyond the
    Python queue. This helper prefers that method when available and then
    falls back to recorder.flush() or a manual queue drain if neither
    exists, so call sites can be agnostic about the concrete recorder type.
    """
    if hasattr(recorder, "flush_input_buffers") and callable(recorder.flush_input_buffers):
        try: recorder.flush_input_buffers(); return
        except Exception: pass
    if hasattr(recorder, "flush") and callable(recorder.flush):
        try: recorder.flush(); return
        except Exception: pass
    for name in ("_q", "q", "_queue", "queue", "input_queue", "_input_q"):
        q = getattr(recorder, name, None)
        if q is None: continue
        try:
            while True: q.get_nowait()
        except Exception: pass

def speak_and_back_to_idle(text: str, recorder, kw=None):
    if not BARGE_IN:
        set_global_listen_state(False)
        recorder.set_listen(False)
        recorder.flush()

    try:
        say(text, recorder=recorder, wakeword=kw)
    finally:
        if not BARGE_IN:
            set_global_listen_state(False)

            post_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5"))
            if post_cd > 0:
                time.sleep(post_cd)


            _flush_input_buffers(recorder)
            if kw is not None:
                kw.reset_after_tts()
            set_global_listen_state(True)
            recorder.set_listen(True)
        else:
            if kw is not None:
                kw.reset_after_tts()

def chat_once(user_text: str) -> str:
    logger.info("[llm] request → %r", user_text)
    buf = ""
    try:
        for chunk in chat_stream(user_text):
            buf += chunk
    except Exception as e:
        logger.error("[llm] error: %s", e)
        return ""
    logger.info("[llm] done (%d chars)", len(buf))
    logger.info("[llm] reply → %r", buf)
    return buf

class ConversationMemory:
    def __init__(self, max_turns: int = 6, system_prompt: str | None = None):
        self.max_turns = int(max_turns)
        self.system_prompt = (system_prompt or "").strip() or None
        self._pairs = collections.deque()
    def reset(self):
        self._pairs.clear()
    def add_user(self, text: str):
        self._last_user = text
    def add_assistant(self, text: str):
        u = getattr(self, "_last_user", None) or ""
        self._pairs.append((u, text))
        while self.max_turns > 0 and len(self._pairs) > self.max_turns:
            self._pairs.popleft()
        self._last_user = None
    def build_messages(self, current_user: str):
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        for u, a in list(self._pairs):
            if u: msgs.append({"role": "user", "content": u})
            if a: msgs.append({"role": "assistant", "content": a})
        msgs.append({"role": "user", "content": current_user})
        return msgs

_conv = ConversationMemory(
    max_turns=int(os.getenv("LLM_CTX_TURNS", "6")),
    system_prompt=os.getenv("LLM_SYSTEM_PROMPT", "")
)

def _run_startup_checks(logger: logging.Logger) -> None:
    check_stt_health(STT_URL, timeout=3.0, logger=logger)
    check_ollama_model(OLLAMA_URL, OLLAMA_MODEL, timeout=3.0, logger=logger)
    if TTS_MODE.lower() == "mqtt" or PIPER_MQTT_HOST:
        check_piper_mqtt_connectivity(
            host=PIPER_MQTT_HOST,
            port=PIPER_MQTT_PORT,
            username=PIPER_MQTT_USERNAME,
            password=PIPER_MQTT_PASSWORD,
            use_tls=PIPER_MQTT_TLS,
            protocol=PIPER_MQTT_PROTOCOL,
            clean_start_supported=_mqtt_client_supports("clean_start"),
            clean_session_supported=_mqtt_client_supports("clean_session"),
            clean_start_flag=getattr(mqtt, "MQTT_CLEAN_START_FIRST_ONLY", 1),
            logger=logger,
        )

def _fallback_chat_once(prompt: str) -> str:
    try:
        url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
        r = requests.post(url, json=payload, timeout=120)
        r.raise_for_status()
        return (r.json().get("response") or "").strip()
    except Exception as e:
        logger.exception(f"[llm] fallback ✗ {e}")
        return ""

def llm_chat_once(user_text: str) -> str:
    use_chat = os.getenv("LLM_USE_CHAT", "1") in ("1","true","True")
    if use_chat:
        try:
            msgs = _conv.build_messages(user_text)
            resp = _ollama_chat(msgs)
            return resp.strip()
        except Exception: pass
    return _fallback_chat_once(user_text).strip()


def _ollama_chat(messages: list[dict], model: str | None = None, temperature: float | None = None, num_predict: int | None = None, timeout: float = 120.0) -> str:
    """
    Sends a chat request to the Ollama API.

    Args:
        messages: List of conversation messages.
        model: Optional model override. Defaults to env OLLAMA_MODEL.
        temperature: Optional temperature override. Defaults to env LLM_TEMPERATURE.
        num_predict: Optional max tokens to generate. Defaults to Ollama default (often 128).
        timeout: Request timeout in seconds. Defaults to 120.0.
    """
    base_url = os.getenv("OLLAMA_URL", "http://192.168.10.161:11434").rstrip('/')
    url = f"{base_url}/api/chat"


    used_model = model if model else os.getenv("OLLAMA_MODEL", "coglet:latest")


    if temperature is not None:
        temp_val = temperature
    else:
        temp_val = float(os.getenv("LLM_TEMPERATURE", "0.3"))

    options = {
        "temperature": temp_val,
        "num_ctx": int(os.getenv("LLM_NUM_CTX", "8192")),
        "num_predict": int(os.getenv("LLM_NUM_PREDICT", "1024")),
    }


    if num_predict is not None:
        options["num_predict"] = num_predict

    payload = {
        "model": used_model,
        "messages": messages,
        "stream": False,
        "options": options,
        "keep_alive": LLM_KEEP_ALIVE,
    }

    try:

        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        js = r.json()

        done_reason = js.get("done_reason")
        if done_reason == "length":
            logger.warning("[llm] response stopped by length limit")

        msg = js.get("message", {}) or {}
        content = msg.get("content", "") or ""

        if not content:
            logger.warning("[llm] empty response; done_reason=%r raw=%r", done_reason, js)

        return content
    except Exception as e:
        logger.error("[llm] Request failed: %s", e)

        raise e

def _pcm16_to_wav_bytes(pcm_bytes: bytes, sr_in: int, sr_out: int | None = None) -> bytes:
    if sr_out is None or sr_out == sr_in:
        data_i16 = np.frombuffer(pcm_bytes, dtype="<i2")
        used_sr  = sr_in
    else:
        x = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        g = math.gcd(sr_in, sr_out)
        up, down = sr_out // g, sr_in // g
        y = resample_poly(x, up, down)
        y = np.clip(y, -1.0, 1.0)
        data_i16 = (y * 32767.0).astype("<i2", copy=False)
        used_sr  = sr_out
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(used_sr)
        w.writeframes(data_i16.tobytes())
    return buf.getvalue()

def _http_stt_request(pcm_bytes: bytes, sample_rate: int) -> dict | None:
    try:
        target_sr = int(os.getenv("STT_RESAMPLE_TO_HZ", "16000"))
        lang      = os.getenv("STT_LANG", "de")
        wav_bytes = _pcm16_to_wav_bytes(pcm_bytes, sample_rate, target_sr)
        url = f"{STT_URL.rstrip('/')}/stt"
        files = {"audio": ("speech.wav", wav_bytes, "audio/wav")}
        data  = {"lang": lang}

        logger.info(f"[stt] http → POST {url} (wav {len(wav_bytes)} bytes)")
        r = requests.post(url, files=files, data=data, timeout=60)
        r.raise_for_status()
        js = r.json()
        return js
    except Exception as e:
        logger.exception("[stt] http ✗ %s", e)
        return None

def stt_transcribe(pcm_bytes: bytes, sample_rate: int) -> dict | None:
    return _http_stt_request(pcm_bytes, sample_rate)


def _uses_xvf_vad_wake() -> bool:
    return WAKEWORD_BACKEND_MODE in XVF_WAKE_BACKENDS or WAKEWORD_BACKEND_MODE == "hybrid"


def _uses_openwakeword() -> bool:
    return WAKEWORD_BACKEND_MODE not in XVF_WAKE_BACKENDS


def main():
    logger.info("[pi] Coglet PI starting v%s", __version__)

    if DEMOMODE:
        demomode()
        return

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    try:
        _run_startup_checks(logger)
    except StartupCheckError as exc:
        logger.error("Startup checks failed: %s", exc)
        sys.exit(1)

    servo_setup = _initialize_all_servos(logger)
    _initialize_status_led()


    mic_hw = None
    if _XVF_MIC_AVAILABLE:
        mic_hw = ReSpeakerMic(logger)
        mic_hw.start()
    else:
        logger.info("[mic] Hardware VAD/DOA not available")


    if MODEL_READY:
        logger.info("[pi] model ready → speak")
        say(MODEL_READY)


    rec = Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR)
    rec.start()
    use_xvf_vad_wake = _uses_xvf_vad_wake() and mic_hw is not None and getattr(mic_hw, "is_connected", False)
    use_openwakeword = _uses_openwakeword() or not use_xvf_vad_wake
    if _uses_xvf_vad_wake() and not use_xvf_vad_wake:
        logger.warning("[pi] WAKEWORD_BACKEND=%s requested, but XVF3800 VAD is unavailable; falling back to OpenWakeWord", WAKEWORD_BACKEND)
    kw = Wakeword(WAKEWORD_BACKEND, OWW_MODEL, OWW_THRESHOLD, hw_sr=rec.sr) if use_openwakeword else None
    logger.info(
        "[pi] Wake trigger: backend=%s xvf_vad=%s openwakeword=%s",
        WAKEWORD_BACKEND_MODE,
        use_xvf_vad_wake,
        use_openwakeword,
    )


    face_tracker = None
    face_tracker_cleanup = None
    try:
        bundle = _setup_face_tracking(logger, servo_setup)
        if bundle:
            face_tracker, face_tracker_cleanup = bundle
            face_tracker.start()
    except Exception as e:
        logger.error("Face tracking setup failed: %s", e)

    exit_requested = False
    last_activity_ts = time.monotonic()
    last_turn_ts = 0.0
    is_deep_sleep = False
    breath_center_pitch = 0.0
    xvf_vad_armed = not use_xvf_vad_wake

    try:
        while not exit_requested and not _shutdown_event.is_set():
            try:
                logger.info("[pi] waiting for speech" if use_xvf_vad_wake and not use_openwakeword else "[pi] waiting for wakeword")

                if not is_deep_sleep:
                    _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                    _start_idle_animation()
                    if use_xvf_vad_wake and not use_openwakeword and XVF_WAKE_HOLD_EYELIDS:
                        _eyelids_set_mode("hold")


                wake_detected = False
                target_doa = None
                wake_source = ""

                while not wake_detected and not _shutdown_event.is_set():
                    if mic_hw:
                        is_speaking, raw_angle = mic_hw.get_status()
                        if is_speaking:
                            logger.debug(f"[mic] Speech detected at {raw_angle}°")
                            target_doa = raw_angle
                            if use_xvf_vad_wake and xvf_vad_armed:
                                wake_detected = True
                                wake_source = "xvf_vad"
                                xvf_vad_armed = False
                                break
                        elif use_xvf_vad_wake:
                            xvf_vad_armed = True

                    if use_xvf_vad_wake and not use_openwakeword and hasattr(rec, "trim_buffer"):
                        rec.trim_buffer(XVF_WAKE_PREROLL_S)

                    if not use_openwakeword or kw is None:
                        now = time.monotonic()
                        if not is_deep_sleep and (now - last_activity_ts > DEEP_SLEEP_TIMEOUT_S):
                            logger.info("[pi] Entering Deep Sleep")
                            is_deep_sleep = True
                            say(DS_ACK)
                            if face_tracker: face_tracker.stop()
                            _stop_idle_animation()
                            _eyelids_set_mode("closed")
                            rec.flush()

                        if is_deep_sleep:
                            phase = math.sin(now * 1.5)
                            set_deep_sleep_led_pulse(phase)

                        time.sleep(0.01)
                        continue

                    processed_chunks = 0
                    max_chunks = 20

                    while True:
                        if kw.check_once(rec):
                            wake_detected = True
                            wake_source = "oww"
                            break

                        processed_chunks += 1
                        q_size = rec.get_queue_size() if hasattr(rec, "get_queue_size") else rec._q.qsize()


                        if q_size == 0:
                            break

                        if processed_chunks >= max_chunks:
                            logger.debug("[audio] Wakeword loop lagging? Processed %d chunks, q_size=%d", processed_chunks, q_size)
                            break

                        if processed_chunks > 1 and q_size < 2:
                            break

                    if wake_detected: break


                    now = time.monotonic()
                    if not is_deep_sleep and (now - last_activity_ts > DEEP_SLEEP_TIMEOUT_S):
                        logger.info("[pi] Entering Deep Sleep")
                        is_deep_sleep = True
                        say(DS_ACK)
                        if face_tracker: face_tracker.stop()
                        _stop_idle_animation()
                        _eyelids_set_mode("closed")
                        rec.flush()


                    if is_deep_sleep:
                        phase = math.sin(now * 1.5)

                        set_deep_sleep_led_pulse(phase)

                    time.sleep(0.01)

                if _shutdown_event.is_set(): break


                if is_deep_sleep:
                    logger.info("[pi] Waking up!")
                    is_deep_sleep = False

                    if hasattr(kw, "reset"): kw.reset()
                    rec.flush()
                    _apply_personality_neutral_pose()
                    _eyelids_set_mode("auto")
                    _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                    if face_tracker:
                        if hasattr(face_tracker._config, 'patrol_enabled'):
                             object.__setattr__(face_tracker._config, 'patrol_enabled', True)
                        face_tracker.start()
                    time.sleep(0.5)

                last_activity_ts = time.monotonic()
                _stop_idle_animation()

                logger.info("[pi] %s detected", "speech" if wake_source == "xvf_vad" else "wakeword")
                direct_vad_capture = wake_source == "xvf_vad" and not use_openwakeword
                if direct_vad_capture:
                    _led_set_state_safe(CogletState.LISTENING)
                else:
                    rec.set_listen(False)


                if not direct_vad_capture and mic_hw and target_doa is not None:
                    raw_angle = target_doa

                    DOA_OFFSET = 0
                    TURN_SPEED = 40.0
                    SEC_PER_DEG = 0.015


                    rel_angle = (raw_angle - DOA_OFFSET) % 360
                    if rel_angle > 180: rel_angle -= 360


                    if -90 <= rel_angle <= 90:

                        if abs(rel_angle) > 10:
                            logger.info(f"[body] Turning body by {rel_angle}°")

                            lwh = _get_anim_servo("LWH")
                            rwh = _get_anim_servo("RWH")

                            if lwh and rwh:
                                duration = abs(rel_angle) * SEC_PER_DEG
                                duration = min(0.8, duration)


                                dir_factor = 1.0 if rel_angle > 0 else -1.0


                                lwh.move_to(TURN_SPEED * dir_factor)
                                rwh.move_to(-TURN_SPEED * dir_factor)
                                lwh.update(1.0)
                                rwh.update(1.0)

                                time.sleep(duration)


                                lwh.move_to(0.0)
                                rwh.move_to(0.0)
                                lwh.update(1.0)
                                rwh.update(1.0)


                                if hasattr(mic_hw, "_silence_counter"):
                                    mic_hw._silence_counter = 50


                if not direct_vad_capture:
                    rec.flush()
                    say(MODEL_CONFIRM)


                    post_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5"))
                    if post_cd > 0:
                        time.sleep(post_cd)
                    rec.flush()
                    rec.set_listen(True)

                if os.getenv("LLM_RESET_ON_WAKE", "1") in ("1", "true"):
                    _conv.reset()


                endpoint = SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr)
                anim_listen_start()
                try:

                    if mic_hw:
                        mic_hw.set_paused(True)
                    pcm, dur = endpoint.record(rec)
                finally:

                    if mic_hw:
                        mic_hw.set_paused(False)
                    anim_listen_stop()

                if len(pcm) < int(0.2 * rec.sr) * 2:
                    logger.info("[pi] silence; back to idle")
                    continue

                last_activity_ts = time.monotonic()
                _led_set_state_safe(CogletState.THINKING)


                stt = stt_transcribe(pcm, rec.sr)
                user_text = (stt.get("text") or "").strip() if stt else ""
                if not user_text:
                    continue

                logger.info("[pi] user: %s", user_text)
                if _is_program_exit_command(user_text):
                    say(MODEL_BYEBYE)
                    break


                if _is_email_request(user_text):
                    _handle_email_request(user_text, rec, kw)
                    last_activity_ts = time.monotonic()

                    continue

                _conv.add_user(user_text)
                anim_think_start()
                try:
                    reply = llm_chat_once(user_text)
                except Exception:
                    anim_think_stop()
                    raise

                if reply:
                    _conv.add_assistant(reply)
                    logger.info("[pi] assistant: %s", reply)
                    speak_and_back_to_idle(clean_tts_text(reply), rec, kw)
                anim_think_stop()
                last_activity_ts = time.monotonic()


                if os.getenv("FOLLOWUP_ENABLE", "1") in ("1", "true", "True"):
                    try: max_turns = int(os.getenv("FOLLOWUP_MAX_TURNS", "10"))
                    except: max_turns = 5
                    arm_s = float(os.getenv("FOLLOWUP_ARM_S", "3.0"))
                    fu_cd = float(os.getenv("FOLLOWUP_COOLDOWN_S", "0.10"))
                    turns = 0

                    while not exit_requested and not _shutdown_event.is_set() and (max_turns == 0 or turns < max_turns):
                        _led_set_state_safe(CogletState.AWAIT_FOLLOWUP)


                        sleep_s = fu_cd
                        if BARGE_IN:
                            try:
                                tts_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5"))
                            except Exception:
                                tts_cd = 0.5
                            sleep_s = max(fu_cd, tts_cd)
                            rec.set_listen(False)

                        time.sleep(sleep_s)
                        rec.flush()
                        if BARGE_IN:
                            rec.set_listen(True)

                        endpoint = SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr)
                        anim_listen_start()
                        try:

                            if mic_hw:
                                mic_hw.set_paused(True)
                            pcm, dur = endpoint.record(rec, no_speech_timeout_s=arm_s)
                        finally:

                            if mic_hw:
                                mic_hw.set_paused(False)
                            anim_listen_stop()

                        if len(pcm) < int(0.2 * rec.sr) * 2:
                            logger.info("[pi] no follow-up detected")
                            say(EOC_ACK)
                            break

                        last_activity_ts = time.monotonic()

                        stt = stt_transcribe(pcm, rec.sr)
                        user_text = (stt.get("text") or "").strip() if stt else ""
                        if not user_text:
                            say(EOC_ACK)
                            break

                        logger.info("[pi] user (fu): %s", user_text)
                        if _is_program_exit_command(user_text):
                            say(MODEL_BYEBYE)
                            exit_requested = True
                            _shutdown_event.set()
                            break

                        norm = _normalize_command_text(user_text)
                        if norm in {"danke", "stop", "nein danke", "tschüss", "byebye"}:
                            say(EOC_ACK)
                            break


                        if _is_email_request(user_text):
                            _handle_email_request(user_text, rec, kw)
                            last_activity_ts = time.monotonic()
                            turns += 1
                            continue

                        _conv.add_user(user_text)
                        anim_think_start()
                        try:
                            reply = llm_chat_once(user_text)
                        except Exception:
                            anim_think_stop()
                            raise

                        if reply:
                            _conv.add_assistant(reply)
                            logger.info("[pi] assistant (fu): %s", reply)
                            speak_and_back_to_idle(clean_tts_text(reply), rec, kw)
                            turns += 1
                        anim_think_stop()
                        last_activity_ts = time.monotonic()

                _led_set_state_safe(CogletState.AWAIT_WAKEWORD)

                rec.flush()
                if hasattr(kw, "reset"): kw.reset()

            except Exception as e:
                logger.exception("[pi] Critical error in main loop: %s", e)

                time.sleep(1.0)

                try:
                    anim_think_stop()
                    anim_listen_stop()
                    anim_talk_stop()
                    _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                except Exception:
                    pass

    except KeyboardInterrupt:
        logger.info("[pi] interrupted.")
        say(MODEL_BYEBYE)
    finally:
        _shutdown_event.set()
        if mic_hw:
            mic_hw.stop()
        if face_tracker_cleanup:
            face_tracker_cleanup()
        _restore_neutral_pose_and_close_lid()
        _cleanup_servo_hardware(servo_setup)
        try:
            rec.stop()
        except:
            pass
        try:
            _led_set_state_safe(CogletState.OFF)
        except:
            pass

if __name__ == "__main__":
    main()
