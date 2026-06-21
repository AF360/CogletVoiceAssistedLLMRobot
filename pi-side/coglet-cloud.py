#!/usr/bin/env python3
"""Run Coglet in OpenAI Realtime cloud mode without a wakeword."""
from __future__ import annotations

import base64
import json
import math
import mimetypes
import os
import queue
import signal
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import command_utils
import numpy as np
import robot_runtime as robot
import startup_checks
import webrtcvad
from hardware.audio import Recorder
from logging_setup import get_logger, setup_logging
from voice_backends.openai_realtime import (
    INPUT_RATE,
    OUTPUT_RATE,
    OpenAIRealtimeConfig,
    RealtimeCallbacks,
    RealtimeSession,
    pcm16_resample,
    _start_aplay_process,
    _terminate_process,
    clear_queue,
)

try:
    import email_sender
except ImportError:
    email_sender = None

setup_logging()
logger = get_logger()
_shutdown_event = threading.Event()
MIC_SR = int(os.getenv("MIC_SR", "16000"))
VAD_AGGR = int(os.getenv("VAD_AGGRESSIVENESS", "2"))
BASE_DIR = Path(__file__).resolve().parent
COGLET_REFERENCE_IMAGES = (
    BASE_DIR / "images" / "coglet1.jpg",
    BASE_DIR / "images" / "coglet2.jpg",
    BASE_DIR / "images" / "coglet3.jpg",
)

ASSISTANT_TRANSCRIPT_DONE = {
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
}
LANG_CODE = (os.getenv("COGLET_LANG") or "de").strip().lower()
if LANG_CODE not in {"de", "en"}:
    LANG_CODE = "de"

_CLOUD_STRINGS = {
    "de": {
        "startup_message": "Ich bin online und bereit zu helfen.",
        "shutdown_message": "Tschüßen!",
        "exact_spoken_prompt": 'Sprich exakt diesen Satz und nichts weiter: "{message}"',
        "email_tool_description": (
            "Sendet den vom Benutzer gewünschten vollständigen Inhalt per E-Mail an "
            "den lokal vorkonfigurierten Besitzer. Verwende dieses Tool, wenn der "
            "Benutzer ausdrücklich bittet, etwas per E-Mail zu senden. Erzeuge einen "
            "sinnvollen Betreff und einen ausführlichen, optisch ansprechenden HTML-Inhalt. "
            "Der body muss valides HTML enthalten, keinen Markdown-Text und keinen reinen "
            "Fließtext. Verwende sinnvolle Absätze, Überschriften und Listen."
        ),
        "email_subject_description": "Kurzer, aussagekräftiger Betreff der E-Mail.",
        "email_body_description": (
            "Vollständiger E-Mail-Inhalt als valides HTML ohne <html>- oder "
            "<body>-Rahmen. Nutze <h2>/<h3> für Überschriften, <p> für Absätze, "
            "<ul>/<ol> mit <li> für Listen und <strong> für wichtige Angaben. "
            "Bei Rezepten: Titel, kurze Einleitung, Zutaten als echte HTML-Liste, "
            "Zubereitung als nummerierte HTML-Liste und optional Tipps in einem "
            "eigenen Abschnitt. Keine Markdown-Syntax und kein einzeiliger Fließtext."
        ),
        "email_success": "Die E-Mail wurde erfolgreich versendet.",
        "reference_images_tool_description": (
            "Lädt Fotos von Coglets wirklichem Roboter-Äußeren in die aktuelle Unterhaltung. "
            "Verwende dieses Tool, wenn der Benutzer Coglets Bilder laden oder ansehen möchte "
            "oder ausdrücklich sagt: Lade deine Bilder."
        ),
        "reference_images_context": (
            "Diese drei Referenzbilder zeigen dich, Coglet, und dein echtes "
            "Roboter-Äußeres aus verschiedenen Perspektiven. Betrachte sie als "
            "visuellen Kontext für das folgende Gespräch über dein Aussehen."
        ),
        "reference_images_loaded": "Coglets Referenzbilder wurden in die Unterhaltung geladen.",
        "reference_images_already_loaded": "Die Referenzbilder sind bereits in dieser Unterhaltung geladen.",
        "exit_phrases": (
            "programm ende",
            "programmende",
            "programm-ende",
            "coglet shutdown",
            "coglet ausschalten",
            "coglet beenden",
        ),
        "native_phrases": (
            "danke",
            "stop",
            "stopp",
            "nein danke",
            "tschüss",
            "tschuss",
            "tschuess",
            "byebye",
            "bye bye",
        ),
    },
    "en": {
        "startup_message": "I am online and ready to help.",
        "shutdown_message": "Goodbye!",
        "exact_spoken_prompt": 'Say exactly this sentence and nothing else: "{message}"',
        "email_tool_description": (
            "Sends the complete content requested by the user by email to the locally "
            "configured owner. Use this tool when the user explicitly asks to send "
            "something by email. Create a meaningful subject and a detailed, visually "
            "structured HTML body. The body must contain valid HTML, no Markdown and no "
            "plain one-line text. Use sensible paragraphs, headings and lists."
        ),
        "email_subject_description": "Short, meaningful email subject.",
        "email_body_description": (
            "Complete email content as valid HTML without <html> or <body> wrappers. "
            "Use <h2>/<h3> for headings, <p> for paragraphs, <ul>/<ol> with <li> for "
            "lists and <strong> for important details. For recipes: title, short "
            "introduction, ingredients as a real HTML list, preparation as a numbered "
            "HTML list and optional tips in their own section. No Markdown syntax and "
            "no one-line plain text."
        ),
        "email_success": "The email was sent successfully.",
        "reference_images_tool_description": (
            "Loads photos of Coglet's real robot appearance into the current conversation. "
            "Use this tool when the user wants to load or see Coglet's pictures, or "
            "explicitly says: load your pictures."
        ),
        "reference_images_context": (
            "These three reference images show you, Coglet, and your real robot "
            "appearance from different perspectives. Treat them as visual context for "
            "the following conversation about your appearance."
        ),
        "reference_images_loaded": "Coglet's reference images have been loaded into the conversation.",
        "reference_images_already_loaded": "The reference images are already loaded in this conversation.",
        "exit_phrases": (
            "program end",
            "end program",
            "coglet shutdown",
            "shut down coglet",
            "coglet turn off",
            "coglet stop",
        ),
        "native_phrases": (
            "thanks",
            "thank you",
            "stop",
            "no thanks",
            "goodbye",
            "bye",
            "bye bye",
        ),
    },
}
CLOUD_TEXT = _CLOUD_STRINGS[LANG_CODE]
DEFAULT_STARTUP_MESSAGE = CLOUD_TEXT["startup_message"]
DEFAULT_SHUTDOWN_MESSAGE = CLOUD_TEXT["shutdown_message"]
DEFAULT_EXIT_PHRASES = tuple(CLOUD_TEXT["exit_phrases"])
CLOUD_NATIVE_PHRASES = set(CLOUD_TEXT["native_phrases"])
LOCAL_BARGE_IN_SAMPLE_RATES = {8000, 16000, 32000, 48000}


def _env_text(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip() or default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_float(name: str, default: float, min_value: float | None = None) -> float:
    try:
        value = float(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    if min_value is not None:
        value = max(min_value, value)
    return value


def _pcm16_dbfs(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2")
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32)))) / 32768.0)
    return 20.0 * math.log10(max(rms, 1e-12))


SEND_EMAIL_TOOL = {
    "type": "function",
    "name": "send_email",
    "description": CLOUD_TEXT["email_tool_description"],
    "parameters": {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "description": CLOUD_TEXT["email_subject_description"],
            },
            "body": {
                "type": "string",
                "description": CLOUD_TEXT["email_body_description"],
            },
        },
        "required": ["subject", "body"],
        "additionalProperties": False,
    },
}
LOAD_COGLET_REFERENCE_IMAGES_TOOL = {
    "type": "function",
    "name": "load_coglet_reference_images",
    "description": CLOUD_TEXT["reference_images_tool_description"],
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}

def _cloud_exit_phrases(normalize: Callable[[str], str]) -> set[str]:
    raw = os.getenv("OPENAI_REALTIME_EXIT_PHRASES", "").strip()
    values = (
        [part.strip() for part in raw.split(",") if part.strip()]
        if raw
        else list(DEFAULT_EXIT_PHRASES)
    )
    return {normalize(value) for value in values if normalize(value)}


def _image_as_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError(f"Unsupported Coglet image format: {path.name}")

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class CloudRealtimeSession(RealtimeSession):
    def __init__(
        self,
        *args: Any,
        exit_matcher: Callable[[str], bool] | None = None,
        normalize_text: Callable[[str], str] | None = None,
        shutdown_message: str | None = DEFAULT_SHUTDOWN_MESSAGE,
        prepare_shutdown: Callable[[], None] | None = None,
        send_email: Callable[[str, str], Mapping[str, Any]] | None = None,
        reference_images: tuple[Path, ...] = COGLET_REFERENCE_IMAGES,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._server_audio_done = threading.Event()
        self._playback_until = 0.0
        self._exit_matcher = exit_matcher
        self._normalize_text = normalize_text or (lambda text: text.casefold().strip())
        self._shutdown_message = (shutdown_message or DEFAULT_SHUTDOWN_MESSAGE).strip() or DEFAULT_SHUTDOWN_MESSAGE
        self._prepare_shutdown = prepare_shutdown
        self._send_email = send_email
        self._reference_images = tuple(reference_images)
        self._reference_images_loaded = False
        self._shutdown_after_playback = False
        self._shutdown_prepare_done = False
        self._shutdown_response_pending = False
        self._shutdown_response_started = False
        self._shutdown_request_lock = threading.Lock()
        self._usage_lock = threading.Lock()
        self._usage_started_at = time.monotonic()
        self._usage_logged = False
        self._usage_seen_response_ids: set[str] = set()
        self._usage = {
            "responses": 0,
            "total_tokens": 0,
            "input_tokens": 0,
            "input_text_tokens": 0,
            "input_audio_tokens": 0,
            "input_image_tokens": 0,
            "cached_tokens": 0,
            "cached_text_tokens": 0,
            "cached_audio_tokens": 0,
            "cached_image_tokens": 0,
            "output_tokens": 0,
            "output_text_tokens": 0,
            "output_audio_tokens": 0,
        }
        self._local_barge_in_enabled = _env_bool("OPENAI_REALTIME_LOCAL_BARGE_IN", True)
        self._barge_in_min_dbfs = _env_float("OPENAI_REALTIME_BARGE_IN_MIN_DBFS", -35.0)
        self._barge_in_frames_required = _env_int("OPENAI_REALTIME_BARGE_IN_FRAMES", 3, min_value=1)
        self._barge_in_cooldown_s = _env_float("OPENAI_REALTIME_BARGE_IN_COOLDOWN_S", 0.8, min_value=0.0)
        self._barge_in_hits = 0
        self._last_barge_in_at = 0.0
        self._barge_in_vad: webrtcvad.Vad | None = None
        recorder_sr = int(getattr(self.recorder, "sr", 0) or 0)
        if self._local_barge_in_enabled and recorder_sr in LOCAL_BARGE_IN_SAMPLE_RATES:
            aggr = _env_int(
                "OPENAI_REALTIME_BARGE_IN_VAD_AGGRESSIVENESS",
                VAD_AGGR,
                min_value=0,
                max_value=3,
            )
            self._barge_in_vad = webrtcvad.Vad(aggr)
            self.callbacks.logger.info(
                "[openai-realtime] Local barge-in enabled: vad=%d min_dbfs=%.1f frames=%d",
                aggr,
                self._barge_in_min_dbfs,
                self._barge_in_frames_required,
            )
        elif self._local_barge_in_enabled:
            self.callbacks.logger.warning(
                "[openai-realtime] Local barge-in disabled; unsupported MIC_SR=%s",
                recorder_sr,
            )

    @staticmethod
    def _token_count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _record_response_usage(self, response: Mapping[str, Any]) -> None:
        response_id = str(response.get("id") or "")
        usage = response.get("usage")
        usage_map = usage if isinstance(usage, Mapping) else {}
        input_details = usage_map.get("input_token_details")
        input_map = input_details if isinstance(input_details, Mapping) else {}
        output_details = usage_map.get("output_token_details")
        output_map = output_details if isinstance(output_details, Mapping) else {}
        cached_details = input_map.get("cached_tokens_details")
        cached_map = cached_details if isinstance(cached_details, Mapping) else {}

        with self._usage_lock:
            if response_id and response_id in self._usage_seen_response_ids:
                return
            if response_id:
                self._usage_seen_response_ids.add(response_id)
            self._usage["responses"] += 1
            self._usage["total_tokens"] += self._token_count(usage_map.get("total_tokens"))
            self._usage["input_tokens"] += self._token_count(usage_map.get("input_tokens"))
            self._usage["input_text_tokens"] += self._token_count(input_map.get("text_tokens"))
            self._usage["input_audio_tokens"] += self._token_count(input_map.get("audio_tokens"))
            self._usage["input_image_tokens"] += self._token_count(input_map.get("image_tokens"))
            self._usage["cached_tokens"] += self._token_count(input_map.get("cached_tokens"))
            self._usage["cached_text_tokens"] += self._token_count(cached_map.get("text_tokens"))
            self._usage["cached_audio_tokens"] += self._token_count(cached_map.get("audio_tokens"))
            self._usage["cached_image_tokens"] += self._token_count(cached_map.get("image_tokens"))
            self._usage["output_tokens"] += self._token_count(usage_map.get("output_tokens"))
            self._usage["output_text_tokens"] += self._token_count(output_map.get("text_tokens"))
            self._usage["output_audio_tokens"] += self._token_count(output_map.get("audio_tokens"))

        self.callbacks.logger.debug(
            "[openai-realtime] Response usage id=%s status=%s total=%d input=%d output=%d cached=%d",
            response_id or "-",
            str(response.get("status") or "-"),
            self._token_count(usage_map.get("total_tokens")),
            self._token_count(usage_map.get("input_tokens")),
            self._token_count(usage_map.get("output_tokens")),
            self._token_count(input_map.get("cached_tokens")),
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _log_session_usage(self) -> None:
        with self._usage_lock:
            if self._usage_logged:
                return
            self._usage_logged = True
            snapshot = dict(self._usage)
        duration = self._format_duration(time.monotonic() - self._usage_started_at)
        self.callbacks.logger.info("[openai-realtime] Session usage:")
        self.callbacks.logger.info("  Responses:          %10d", snapshot["responses"])
        self.callbacks.logger.info("  Input tokens:       %10d", snapshot["input_tokens"])
        self.callbacks.logger.info("    Text:             %10d", snapshot["input_text_tokens"])
        self.callbacks.logger.info("    Audio:            %10d", snapshot["input_audio_tokens"])
        if snapshot["input_image_tokens"]:
            self.callbacks.logger.info("    Image:            %10d", snapshot["input_image_tokens"])
        self.callbacks.logger.info("    Cached:           %10d", snapshot["cached_tokens"])
        if (
            snapshot["cached_text_tokens"]
            or snapshot["cached_audio_tokens"]
            or snapshot["cached_image_tokens"]
        ):
            self.callbacks.logger.info("      Text:           %10d", snapshot["cached_text_tokens"])
            self.callbacks.logger.info("      Audio:          %10d", snapshot["cached_audio_tokens"])
            if snapshot["cached_image_tokens"]:
                self.callbacks.logger.info("      Image:          %10d", snapshot["cached_image_tokens"])
        self.callbacks.logger.info("  Output tokens:      %10d", snapshot["output_tokens"])
        self.callbacks.logger.info("    Text:             %10d", snapshot["output_text_tokens"])
        self.callbacks.logger.info("    Audio:            %10d", snapshot["output_audio_tokens"])
        self.callbacks.logger.info("  Total tokens:       %10d", snapshot["total_tokens"])
        self.callbacks.logger.info("  Session duration:   %10s", duration)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._log_session_usage()

    def connect(self) -> None:
        super().connect()
        if self.ws is not None and hasattr(self.ws, "settimeout"):
            self.ws.settimeout(None)
        self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "tools": [
                        SEND_EMAIL_TOOL,
                        LOAD_COGLET_REFERENCE_IMAGES_TOOL,
                    ],
                    "tool_choice": "auto",
                },
            }
        )
        self.callbacks.logger.info(
            "[openai-realtime] Connected; WebSocket read timeout disabled; "
            "send_email and load_coglet_reference_images tools configured"
        )

    def _request_exact_spoken_response(self, text: str, log_label: str) -> None:
        message = text.strip()
        if not message:
            return
        self.callbacks.logger.info("[openai-realtime] %s: %s", log_label, message)
        self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": CLOUD_TEXT["exact_spoken_prompt"].format(message=message),
                        }
                    ],
                },
            }
        )
        self._send({"type": "response.create"})

    def announce_startup(self, text: str) -> None:
        self._request_exact_spoken_response(text, "Startup announcement")

    def _start_pending_shutdown_response(self) -> None:
        with self._shutdown_request_lock:
            if not self._shutdown_response_pending or self._shutdown_response_started:
                return
            self._shutdown_response_pending = False
            self._shutdown_response_started = True
        self._server_audio_done.clear()
        try:
            self._request_exact_spoken_response(
                self._shutdown_message,
                "Shutdown announcement",
            )
        except Exception as exc:
            self.callbacks.logger.error(
                "[openai-realtime] Could not start shutdown announcement: %s", exc
            )
            self.shutdown_event.set()

    def request_shutdown_with_farewell(self, source: str) -> bool:
        run_prepare = False
        with self._shutdown_request_lock:
            if self._shutdown_after_playback or self.shutdown_event.is_set():
                return False
            self._shutdown_after_playback = True
            self._shutdown_response_pending = True
            if not self._shutdown_prepare_done:
                self._shutdown_prepare_done = True
                run_prepare = True
        self.callbacks.logger.info(
            "[openai-realtime] Graceful shutdown requested (%s)", source
        )
        try:
            if run_prepare and self._prepare_shutdown is not None:
                self._prepare_shutdown()
            self.interrupt(force_server_cancel=True)
        except Exception as exc:
            self.callbacks.logger.error(
                "[openai-realtime] Could not prepare shutdown announcement: %s", exc
            )
            self.shutdown_event.set()
        return True

    def _load_reference_images(self) -> Mapping[str, Any]:
        if self._reference_images_loaded:
            return {
                "success": True,
                "already_loaded": True,
                "images_loaded": 0,
                "message": CLOUD_TEXT["reference_images_already_loaded"],
            }

        missing = [str(path) for path in self._reference_images if not path.is_file()]
        if missing:
            raise FileNotFoundError(
                "Coglet reference images are missing: " + ", ".join(missing)
            )

        content: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": CLOUD_TEXT["reference_images_context"],
            }
        ]

        for image_path in self._reference_images:
            content.append(
                {
                    "type": "input_image",
                    "image_url": _image_as_data_url(image_path),
                }
            )

        self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": content,
                },
            }
        )

        self._reference_images_loaded = True
        self.callbacks.logger.info(
            "[openai-realtime] Loaded %d Coglet reference images",
            len(self._reference_images),
        )
        return {
            "success": True,
            "already_loaded": False,
            "images_loaded": len(self._reference_images),
            "message": CLOUD_TEXT["reference_images_loaded"],
        }

    def _handle_function_calls(self, response: Mapping[str, Any]) -> bool:
        outputs = response.get("output")
        if not isinstance(outputs, list):
            return False

        handled = False
        for item in outputs:
            if not isinstance(item, Mapping) or item.get("type") != "function_call":
                continue
            handled = True
            name = str(item.get("name") or "")
            call_id = str(item.get("call_id") or "")
            raw_arguments = str(item.get("arguments") or "{}")
            result: Mapping[str, Any]

            if not call_id:
                result = {"success": False, "error": "Missing call_id"}
            else:
                try:
                    if name == "send_email":
                        arguments = json.loads(raw_arguments)
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments must be an object")
                        subject = str(arguments.get("subject") or "").strip()
                        body = str(arguments.get("body") or "").strip()
                        if not subject or not body:
                            raise ValueError("subject and body are required")
                        if self._send_email is None:
                            raise RuntimeError("email sender is not available")
                        self.callbacks.logger.info(
                            "[openai-realtime] send_email tool call: subject=%r, body=%d chars",
                            subject,
                            len(body),
                        )
                        result = self._send_email(subject, body)
                    elif name == "load_coglet_reference_images":
                        result = self._load_reference_images()
                    else:
                        result = {
                            "success": False,
                            "error": f"Unknown tool: {name}",
                        }
                except Exception as exc:
                    self.callbacks.logger.error(
                        "[openai-realtime] %s tool failed: %s",
                        name or "unknown",
                        exc,
                    )
                    result = {"success": False, "error": str(exc)}

            if call_id:
                self._send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result, ensure_ascii=False),
                        },
                    }
                )

        if handled:
            self._send({"type": "response.create"})
        return handled

    def handle_event(self, event: Mapping[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type in ASSISTANT_TRANSCRIPT_DONE:
            text = str(event.get("transcript") or event.get("text") or "").strip()
            if text and self.config.log_transcripts:
                self.callbacks.logger.info("[openai-realtime] assistant: %s", text)
            return
        if event_type == "error":
            error = event.get("error") if isinstance(event.get("error"), Mapping) else {}
            code = str(error.get("code") or "")
            if code == "response_cancel_not_active":
                self.callbacks.logger.debug(
                    "[openai-realtime] No active response to cancel"
                )
                self._start_pending_shutdown_response()
                return
            if (
                code == "conversation_already_has_active_response"
                and self._shutdown_response_pending
            ):
                self.callbacks.logger.debug(
                    "[openai-realtime] Active response still finishing before farewell"
                )
                return
        super().handle_event(event)

    def _handle_transcript(self, event: Mapping[str, Any]) -> None:
        text = str(event.get("transcript") or event.get("text") or "").strip()
        if not text:
            return
        if self.config.log_transcripts:
            self.callbacks.transcript(text)
        key = text.casefold()
        if key in self._local_command_seen:
            return
        if self._exit_matcher is not None and self._exit_matcher(text):
            self._local_command_seen.add(key)
            self.callbacks.logger.info(
                "[openai-realtime] Cloud shutdown command recognized"
            )
            self.request_shutdown_with_farewell("voice command")
            return
        normalized = self._normalize_text(text)
        if normalized in CLOUD_NATIVE_PHRASES:
            return
        if self.callbacks.local_command(text):
            self._local_command_seen.add(key)
            self.interrupt()
            self.stop_event.set()

    def _maybe_interrupt_for_local_barge_in(self, pcm: bytes) -> None:
        if self._barge_in_vad is None or not self.talking:
            self._barge_in_hits = 0
            return
        now = time.monotonic()
        if now - self._last_barge_in_at < self._barge_in_cooldown_s:
            return
        sample_rate = int(getattr(self.recorder, "sr", MIC_SR) or MIC_SR)
        try:
            is_speech = self._barge_in_vad.is_speech(pcm, sample_rate)
        except Exception as exc:
            self.callbacks.logger.debug(
                "[openai-realtime] Local barge-in VAD skipped: %s",
                exc,
            )
            self._barge_in_hits = 0
            return
        if is_speech and _pcm16_dbfs(pcm) >= self._barge_in_min_dbfs:
            self._barge_in_hits += 1
        else:
            self._barge_in_hits = 0
        if self._barge_in_hits < self._barge_in_frames_required:
            return
        self.callbacks.logger.info(
            "[openai-realtime] Local barge-in detected; interrupting playback"
        )
        self._last_barge_in_at = now
        self._barge_in_hits = 0
        self.interrupt()

    def _microphone_loop(self) -> None:
        frame_ms = 20
        sample_rate = int(getattr(self.recorder, "sr", MIC_SR) or MIC_SR)
        chunk_bytes = max(1, int(sample_rate * frame_ms / 1000.0)) * 2
        while not self.stop_event.is_set() and not self.shutdown_event.is_set():
            try:
                pcm_native = self.recorder.read_bytes(chunk_bytes)
                self._maybe_interrupt_for_local_barge_in(pcm_native)
                pcm = pcm16_resample(pcm_native, sample_rate, INPUT_RATE)
                self._send(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm).decode("ascii"),
                    }
                )
            except Exception as exc:
                self.callbacks.logger.error(
                    "[openai-realtime] Microphone loop stopped: %s",
                    exc,
                )
                self.stop_event.set()
                break

    def interrupt(self, force_server_cancel: bool = False) -> None:
        clear_queue(self.output_queue)
        self.audio_interrupt_event.set()
        if self.talking:
            self.callbacks.stop_talk()
            self.talking = False
        self.callbacks.set_state("listening")
        if self.response_active or force_server_cancel:
            try:
                self._send({"type": "response.cancel"})
            except Exception as exc:
                self.callbacks.logger.debug(
                    "[openai-realtime] response.cancel failed: %s", exc
                )
        if self.current_item_id and (self.response_active or self.talking):
            try:
                self._send(
                    {
                        "type": "conversation.item.truncate",
                        "item_id": self.current_item_id,
                        "content_index": 0,
                        "audio_end_ms": 0,
                    }
                )
            except Exception as exc:
                self.callbacks.logger.debug(
                    "[openai-realtime] item truncate failed: %s", exc
                )
        self.current_item_id = ""

    def _handle_audio_delta(self, event: Mapping[str, Any]) -> None:
        self._server_audio_done.clear()
        super()._handle_audio_delta(event)

    def _handle_response_done(self, event: Mapping[str, Any]) -> None:
        self.response_active = False
        self.callbacks.stop_think()

        response = event.get("response")
        response_map = response if isinstance(response, Mapping) else {}
        self._record_response_usage(response_map)

        if self._shutdown_response_pending and not self._shutdown_response_started:
            self._start_pending_shutdown_response()
            return

        if self._handle_function_calls(response_map):
            return

        self._server_audio_done.set()
        status = response_map.get("status")
        if status == "failed":
            if self._shutdown_after_playback:
                self.shutdown_event.set()
            self.stop_event.set()

    def _finish_talking_after_playback(self) -> None:
        if self.talking:
            self.callbacks.stop_talk()
            self.talking = False
        self.current_item_id = ""
        if self._shutdown_after_playback:
            self.callbacks.logger.info(
                "[openai-realtime] Shutdown announcement finished; stopping Coglet"
            )
            self.shutdown_event.set()
            return
        self.callbacks.set_state("await_followup")

    def _playback_loop(self) -> None:
        proc = _start_aplay_process(OUTPUT_RATE)
        try:
            while not self.stop_event.is_set():
                if self.audio_interrupt_event.is_set():
                    _terminate_process(proc)
                    clear_queue(self.output_queue)
                    self.audio_interrupt_event.clear()
                    self._server_audio_done.clear()
                    self._playback_until = 0.0
                    proc = _start_aplay_process(OUTPUT_RATE)
                    continue
                try:
                    pcm = self.output_queue.get(timeout=0.02)
                except queue.Empty:
                    if self._server_audio_done.is_set() and self.talking:
                        remaining = self._playback_until - time.monotonic()
                        if remaining > 0:
                            time.sleep(min(remaining, 0.02))
                        else:
                            self._server_audio_done.clear()
                            self._finish_talking_after_playback()
                    continue
                try:
                    if proc.stdin:
                        proc.stdin.write(pcm)
                        proc.stdin.flush()
                    duration_s = len(pcm) / (2.0 * OUTPUT_RATE)
                    now = time.monotonic()
                    self._playback_until = max(now, self._playback_until) + duration_s
                except Exception as exc:
                    self.callbacks.logger.error(
                        "[openai-realtime] Playback failed: %s", exc
                    )
                    _terminate_process(proc)
                    self._playback_until = 0.0
                    proc = _start_aplay_process(OUTPUT_RATE)
        finally:
            _terminate_process(proc)


def main() -> int:
    session_holder: dict[str, CloudRealtimeSession | None] = {"session": None}
    logger.info("[pi] Coglet cloud mode starting (OpenAI Realtime, no wakeword)")

    def _request_shutdown(signum=None, frame=None) -> None:
        del frame
        session = session_holder["session"]
        source = "Ctrl+C" if signum == signal.SIGINT else "SIGTERM"
        if session is None:
            _shutdown_event.set()
            return
        if not session.request_shutdown_with_farewell(source):
            logger.warning(
                "[openai-realtime] Second shutdown signal; stopping immediately"
            )
            _shutdown_event.set()

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    try:
        startup_checks.check_openai_realtime_config(
            os.environ,
            logger=logger,
        )
    except startup_checks.StartupCheckError as exc:
        logger.error("Startup checks failed: %s", exc)
        return 1

    config = OpenAIRealtimeConfig.from_env()
    servo_setup = None
    recorder = None
    face_tracker = None
    face_tracker_cleanup = None
    mic_hw = None

    try:
        servo_setup = robot.initialize_all_servos(logger)
        robot.initialize_status_led()
        robot.eyelids_set_mode("auto")
        if robot.XVF_MIC_AVAILABLE and robot.ReSpeakerMic is not None:
            mic_hw = robot.ReSpeakerMic(logger)
            mic_hw.start()
        else:
            logger.info("[mic] Hardware VAD/DOA not available")
        recorder = Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR)
        recorder.start()

        try:
            bundle = robot.setup_face_tracking(logger, servo_setup)
            if bundle:
                face_tracker, face_tracker_cleanup = bundle
                face_tracker.start()
        except Exception as exc:
            logger.error("Face tracking setup failed: %s", exc)

        exit_phrases = _cloud_exit_phrases(command_utils.normalize_command_text)
        logger.info(
            "[openai-realtime] Exit phrases: %s",
            ", ".join(sorted(exit_phrases)),
        )

        def _is_cloud_exit_command(text: str) -> bool:
            return command_utils.normalize_command_text(text) in exit_phrases

        def _prepare_graceful_shutdown() -> None:
            if face_tracker is None:
                return
            logger.info("[openai-realtime] Stopping face tracking before farewell")
            try:
                face_tracker.stop()
            except Exception as exc:
                logger.warning(
                    "Face tracker stop before farewell failed: %s",
                    exc,
                )

        def _send_email(subject: str, body: str) -> Mapping[str, Any]:
            email_to = (os.getenv("EMAIL_TO") or "").strip()
            if not email_to:
                raise RuntimeError("EMAIL_TO is not configured")
            if email_sender is None:
                raise RuntimeError("email_sender module is not available")
            email_sender.send_email_smtp(email_to, subject, body)
            logger.info("[email] Sent successfully to %s", email_to)
            return {
                "success": True,
                "recipient": email_to,
                "message": CLOUD_TEXT["email_success"],
            }

        callbacks = RealtimeCallbacks(
            set_state=robot.led_set_state_safe,
            start_listen=robot.anim_listen_start,
            stop_listen=robot.anim_listen_stop,
            start_think=robot.anim_think_start,
            stop_think=robot.anim_think_stop,
            start_talk=robot.anim_talk_start,
            stop_talk=robot.anim_talk_stop,
            transcript=lambda text: logger.info(
                "[openai-realtime] user: %s",
                text,
            ),
            local_command=lambda text: False,
            logger=logger,
        )
        shutdown_message = _env_text(
            "OPENAI_REALTIME_SHUTDOWN_MESSAGE",
            DEFAULT_SHUTDOWN_MESSAGE,
        )
        session = CloudRealtimeSession(
            config,
            recorder,
            callbacks,
            shutdown_event=_shutdown_event,
            exit_matcher=_is_cloud_exit_command,
            normalize_text=command_utils.normalize_command_text,
            shutdown_message=shutdown_message,
            prepare_shutdown=_prepare_graceful_shutdown,
            send_email=_send_email,
        )
        session_holder["session"] = session
        robot.led_set_state_safe(robot.CogletState.AWAIT_FOLLOWUP)
        logger.info(
            "[openai-realtime] Session starts immediately; server VAD is active"
        )
        session.start()
        session.announce_startup(
            _env_text("OPENAI_REALTIME_STARTUP_MESSAGE", DEFAULT_STARTUP_MESSAGE)
        )
        session.wait_until_done(timeout_s=None)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.exception(
            "[openai-realtime] Continuous cloud session failed: %s",
            exc,
        )
        return 1
    finally:
        session_holder["session"] = None
        _shutdown_event.set()
        try:
            if recorder is not None:
                recorder.stop()
        except Exception:
            pass
        try:
            if mic_hw is not None:
                mic_hw.stop()
        except Exception:
            pass
        try:
            if face_tracker_cleanup is not None:
                face_tracker_cleanup()
        except Exception:
            pass
        try:
            robot.restore_neutral_pose_and_close_lid()
        except Exception:
            pass
        robot.cleanup_servo_hardware(servo_setup)
        robot.led_set_state_safe(robot.CogletState.OFF)


if __name__ == "__main__":
    raise SystemExit(main())
