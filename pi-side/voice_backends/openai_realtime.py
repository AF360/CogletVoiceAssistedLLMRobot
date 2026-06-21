"""OpenAI Realtime speech-to-speech backend for Coglet.

This module is intentionally testable without Raspberry Pi hardware or a live
OpenAI API call. Production code passes recorder/audio callbacks in from
coglet-local.py; tests use fakes.
"""

from __future__ import annotations

import base64
import json
import math
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
from scipy.signal import resample_poly

from .base import BackendContext, BackendResult

REALTIME_WS_URL = "wss://api.openai.com/v1/realtime"
INPUT_RATE = 24000
OUTPUT_RATE = 24000
DEFAULT_VOICE = "marin"
SUPPORTED_VAD = {"server_vad", "semantic_vad"}
INPUT_TRANSCRIPT_EVENTS = {
    "conversation.item.input_audio_transcription.completed",
    "input_audio_buffer.transcription_completed",
}
OUTPUT_DELTA_EVENTS = {
    "response.output_audio.delta",
    "response.audio.delta",
}
OUTPUT_DONE_EVENTS = {
    "response.output_audio.done",
    "response.audio.done",
}


class RealtimeConfigError(ValueError):
    """Raised when Realtime configuration is invalid."""


@dataclass(frozen=True)
class OpenAIRealtimeConfig:
    api_key: str
    model: str = "gpt-realtime-2"
    voice: str = DEFAULT_VOICE
    reasoning_effort: str = "low"
    vad_mode: str = "server_vad"
    vad_eagerness: str = "auto"
    transcription: bool = True
    transcription_model: str = "gpt-4o-mini-transcribe"
    connect_timeout_s: float = 10.0
    log_transcripts: bool = True
    fallback_local: bool = True
    instructions: str = ""
    safety_identifier: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OpenAIRealtimeConfig":
        env = env or os.environ
        api_key = (env.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RealtimeConfigError("OPENAI_API_KEY is required for OpenAI Realtime mode")
        vad_mode = (env.get("OPENAI_REALTIME_VAD_MODE") or "server_vad").strip().lower()
        if vad_mode not in SUPPORTED_VAD:
            raise RealtimeConfigError(
                f"OPENAI_REALTIME_VAD_MODE must be one of {sorted(SUPPORTED_VAD)}"
            )
        instructions = load_instructions(env)
        return cls(
            api_key=api_key,
            model=(env.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2").strip(),
            voice=(env.get("OPENAI_REALTIME_VOICE") or DEFAULT_VOICE).strip(),
            reasoning_effort=(env.get("OPENAI_REALTIME_REASONING_EFFORT") or "low").strip(),
            vad_mode=vad_mode,
            vad_eagerness=(env.get("OPENAI_REALTIME_VAD_EAGERNESS") or "auto").strip(),
            transcription=_env_bool(env, "OPENAI_REALTIME_TRANSCRIPTION", True),
            transcription_model=(env.get("OPENAI_REALTIME_TRANSCRIPTION_MODEL") or "gpt-4o-mini-transcribe").strip(),
            connect_timeout_s=float(env.get("OPENAI_REALTIME_CONNECT_TIMEOUT_S") or "10"),
            log_transcripts=_env_bool(env, "OPENAI_REALTIME_LOG_TRANSCRIPTS", True),
            fallback_local=_env_bool(env, "OPENAI_REALTIME_FALLBACK_LOCAL", True),
            instructions=instructions,
            safety_identifier=(env.get("OPENAI_REALTIME_SAFETY_IDENTIFIER") or "").strip(),
        )


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _realtime_prompt_path_for_lang(lang: str) -> Path:
    normalized = (lang or "de").strip().lower()
    if normalized not in {"de", "en"}:
        normalized = "de"
    return Path(__file__).resolve().parent.parent / "prompts" / f"openai-realtime-coglet-{normalized}.txt"


def load_instructions(env: Mapping[str, str]) -> str:
    inline = (env.get("OPENAI_REALTIME_INSTRUCTIONS") or "").strip()
    if inline:
        return inline
    path = (env.get("OPENAI_REALTIME_INSTRUCTIONS_FILE") or "").strip()
    if path:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    lang = (env.get("COGLET_LANG") or "de").lower().strip()
    prompt_path = _realtime_prompt_path_for_lang(lang)
    with open(prompt_path, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def websocket_headers(config: OpenAIRealtimeConfig) -> list[str]:
    headers = [f"Authorization: Bearer {config.api_key}"]
    if config.safety_identifier:
        headers.append(f"OpenAI-Safety-Identifier: {config.safety_identifier}")
    return headers


def websocket_url(config: OpenAIRealtimeConfig) -> str:
    return f"{REALTIME_WS_URL}?model={config.model}"


def build_session_update(config: OpenAIRealtimeConfig) -> dict[str, Any]:
    turn_detection: dict[str, Any] = {
        "type": config.vad_mode,
        "create_response": True,
        "interrupt_response": True,
    }
    if config.vad_mode == "semantic_vad":
        turn_detection["eagerness"] = config.vad_eagerness

    input_audio: dict[str, Any] = {
        "format": {"type": "audio/pcm", "rate": INPUT_RATE},
        "turn_detection": turn_detection,
    }
    if config.transcription:
        input_audio["transcription"] = {"model": config.transcription_model}

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": config.instructions,
            "reasoning": {"effort": config.reasoning_effort},
            "audio": {
                "input": input_audio,
                "output": {
                    "format": {"type": "audio/pcm", "rate": OUTPUT_RATE},
                    "voice": config.voice,
                },
            },
        },
    }


def pcm16_resample(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    if not pcm_bytes or src_rate == dst_rate:
        return pcm_bytes
    samples = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
    common = math.gcd(int(src_rate), int(dst_rate))
    resampled = resample_poly(samples, int(dst_rate) // common, int(src_rate) // common)
    clipped = np.clip(resampled, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2", copy=False).tobytes()


def clear_queue(q: queue.Queue[bytes]) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        return


@dataclass
class RealtimeCallbacks:
    set_state: Callable[[str], None]
    start_listen: Callable[[], None]
    stop_listen: Callable[[], None]
    start_think: Callable[[], None]
    stop_think: Callable[[], None]
    start_talk: Callable[[], None]
    stop_talk: Callable[[], None]
    transcript: Callable[[str], None]
    local_command: Callable[[str], bool]
    logger: Any


class RealtimeSession:
    """Owns one OpenAI Realtime WebSocket conversation session."""

    def __init__(
        self,
        config: OpenAIRealtimeConfig,
        recorder: Any,
        callbacks: RealtimeCallbacks,
        *,
        websocket_factory: Callable[..., Any] | None = None,
        audio_player: Callable[[queue.Queue[bytes], threading.Event, int], None] | None = None,
        shutdown_event: threading.Event | None = None,
        output_queue_size: int = 64,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.callbacks = callbacks
        self.websocket_factory = websocket_factory or _default_websocket_factory
        self.audio_player = audio_player
        self.shutdown_event = shutdown_event or threading.Event()
        self.stop_event = threading.Event()
        self.send_lock = threading.Lock()
        self.output_queue: queue.Queue[bytes] = queue.Queue(maxsize=output_queue_size)
        self.audio_interrupt_event = threading.Event()
        self.ws: Any = None
        self.threads: list[threading.Thread] = []
        self.response_active = False
        self.talking = False
        self.current_item_id = ""
        self._local_command_seen: set[str] = set()
        self.last_activity_ts = time.monotonic()

    def connect(self) -> None:
        self.ws = self.websocket_factory(
            websocket_url(self.config),
            header=websocket_headers(self.config),
            timeout=self.config.connect_timeout_s,
        )
        self._send(build_session_update(self.config))

    def start(self) -> None:
        self.connect()
        playback_target: Callable[..., Any]
        playback_args: tuple[Any, ...]
        if self.audio_player is None:
            playback_target = self._playback_loop
            playback_args = ()
        else:
            playback_target = self.audio_player
            playback_args = (self.output_queue, self.stop_event, OUTPUT_RATE)
        self.threads = [
            threading.Thread(target=self._receive_loop, name="RealtimeReceive", daemon=True),
            threading.Thread(target=self._microphone_loop, name="RealtimeMicrophone", daemon=True),
            threading.Thread(target=playback_target, args=playback_args, name="RealtimePlayback", daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def wait_until_done(self, timeout_s: float | None = None) -> None:
        while not self.stop_event.is_set() and not self.shutdown_event.is_set():
            idle_s = time.monotonic() - self.last_activity_ts
            if timeout_s is not None and idle_s > timeout_s and not self.response_active and not self.talking:
                self.callbacks.logger.info("[openai-realtime] Follow-up timeout; closing session")
                break
            time.sleep(0.05)
        self.close()

    def close(self) -> None:
        self.stop_event.set()
        clear_queue(self.output_queue)
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception as exc:
            self.callbacks.logger.debug("[openai-realtime] WebSocket close failed: %s", exc)
        for thread in list(self.threads):
            if thread is threading.current_thread():
                continue
            thread.join(timeout=2.0)
        if self.talking:
            self.callbacks.stop_talk()
            self.talking = False
        self.callbacks.stop_think()
        self.callbacks.stop_listen()
        self.callbacks.set_state("await_wakeword")

    def _send(self, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, ensure_ascii=False)
        with self.send_lock:
            self.ws.send(message)

    def _microphone_loop(self) -> None:
        frame_ms = 20
        chunk_bytes = max(1, int(self.recorder.sr * frame_ms / 1000.0)) * 2
        while not self.stop_event.is_set() and not self.shutdown_event.is_set():
            try:
                pcm = self.recorder.read_bytes(chunk_bytes)
                pcm = pcm16_resample(pcm, int(self.recorder.sr), INPUT_RATE)
                self._send({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode("ascii"),
                })
            except Exception as exc:
                self.callbacks.logger.error("[openai-realtime] Microphone loop stopped: %s", exc)
                self.stop_event.set()
                break

    def _playback_loop(self) -> None:
        proc = _start_aplay_process(OUTPUT_RATE)
        try:
            while not self.stop_event.is_set():
                if self.audio_interrupt_event.is_set():
                    _terminate_process(proc)
                    clear_queue(self.output_queue)
                    self.audio_interrupt_event.clear()
                    proc = _start_aplay_process(OUTPUT_RATE)
                    continue
                try:
                    pcm = self.output_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    if proc.stdin:
                        proc.stdin.write(pcm)
                        proc.stdin.flush()
                except Exception as exc:
                    self.callbacks.logger.error("[openai-realtime] Playback failed: %s", exc)
                    _terminate_process(proc)
                    proc = _start_aplay_process(OUTPUT_RATE)
        finally:
            _terminate_process(proc)

    def _receive_loop(self) -> None:
        while not self.stop_event.is_set() and not self.shutdown_event.is_set():
            try:
                raw = self.ws.recv()
            except Exception as exc:
                self.callbacks.logger.error("[openai-realtime] WebSocket receive failed: %s", exc)
                self.stop_event.set()
                break
            if not raw:
                self.callbacks.logger.info("[openai-realtime] WebSocket closed by server")
                self.stop_event.set()
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                self.callbacks.logger.warning("[openai-realtime] Ignoring malformed JSON event")
                continue
            self.handle_event(event)

    def handle_event(self, event: Mapping[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type:
            self.last_activity_ts = time.monotonic()
        if event_type == "session.created" or event_type == "session.updated":
            self.callbacks.set_state("await_followup")
        elif event_type == "input_audio_buffer.speech_started":
            if self.talking:
                self.interrupt()
            self.callbacks.stop_talk()
            self.callbacks.stop_think()
            self.callbacks.start_listen()
            self.callbacks.set_state("listening")
        elif event_type == "input_audio_buffer.speech_stopped":
            self.callbacks.stop_listen()
            self.callbacks.start_think()
            self.callbacks.set_state("thinking")
        elif event_type in OUTPUT_DELTA_EVENTS:
            self._handle_audio_delta(event)
        elif event_type in OUTPUT_DONE_EVENTS or event_type == "response.done":
            self._handle_response_done(event)
        elif event_type in INPUT_TRANSCRIPT_EVENTS:
            self._handle_transcript(event)
        elif event_type == "error":
            self.callbacks.logger.error("[openai-realtime] API error: %s", event.get("error") or event)
            self.stop_event.set()
        else:
            self.callbacks.logger.debug("[openai-realtime] event=%s", event_type)

    def _handle_audio_delta(self, event: Mapping[str, Any]) -> None:
        delta = event.get("delta") or event.get("audio") or ""
        if not isinstance(delta, str) or not delta:
            return
        if not self.talking:
            self.callbacks.stop_think()
            self.callbacks.start_talk()
            self.callbacks.set_state("speaking")
            self.talking = True
        self.response_active = True
        if isinstance(event.get("item_id"), str):
            self.current_item_id = str(event["item_id"])
        try:
            self.output_queue.put_nowait(base64.b64decode(delta))
        except queue.Full:
            self.callbacks.logger.warning("[openai-realtime] Output audio queue full; dropping delta")

    def _handle_response_done(self, event: Mapping[str, Any]) -> None:
        self.response_active = False
        if self.talking:
            self.callbacks.stop_talk()
            self.talking = False
        self.callbacks.stop_think()
        self.callbacks.set_state("await_followup")
        status = ((event.get("response") or {}) if isinstance(event.get("response"), dict) else {}).get("status")
        if status == "failed":
            self.stop_event.set()

    def _handle_transcript(self, event: Mapping[str, Any]) -> None:
        text = str(event.get("transcript") or event.get("text") or "").strip()
        if not text:
            return
        if self.config.log_transcripts:
            self.callbacks.transcript(text)
        key = text.casefold()
        if key in self._local_command_seen:
            return
        if self.callbacks.local_command(text):
            self._local_command_seen.add(key)
            self.interrupt()
            self.stop_event.set()

    def interrupt(self) -> None:
        clear_queue(self.output_queue)
        self.audio_interrupt_event.set()
        if self.talking:
            self.callbacks.stop_talk()
            self.talking = False
        self.callbacks.set_state("listening")
        try:
            self._send({"type": "response.cancel"})
        except Exception as exc:
            self.callbacks.logger.debug("[openai-realtime] response.cancel failed: %s", exc)
        if self.current_item_id:
            try:
                self._send({
                    "type": "conversation.item.truncate",
                    "item_id": self.current_item_id,
                    "content_index": 0,
                    "audio_end_ms": 0,
                })
            except Exception as exc:
                self.callbacks.logger.debug("[openai-realtime] item truncate failed: %s", exc)


def _default_websocket_factory(url: str, header: list[str], timeout: float) -> Any:
    import websocket

    return websocket.create_connection(url, header=header, timeout=timeout)


def _aplay_command(sample_rate: int) -> list[str]:
    cmd = [
        "aplay",
        "-q",
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        "1",
        "-t",
        "raw",
    ]
    spk = os.getenv("SPEAKER_DEVICE", "")
    if spk:
        cmd += ["-D", spk]
    cmd.append("-")
    return cmd


def _start_aplay_process(sample_rate: int) -> subprocess.Popen:
    return subprocess.Popen(
        _aplay_command(sample_rate),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=0.5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def play_pcm_queue_aplay(q: queue.Queue[bytes], stop_event: threading.Event, sample_rate: int) -> None:
    cmd = _aplay_command(sample_rate)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while not stop_event.is_set():
            try:
                pcm = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if proc.stdin:
                proc.stdin.write(pcm)
                proc.stdin.flush()
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass


class OpenAIRealtimeBackend:
    name = "openai_realtime"

    def __init__(self, config: OpenAIRealtimeConfig | None = None) -> None:
        self.config = config or OpenAIRealtimeConfig.from_env()

    def handle_wake_session(self, context: BackendContext) -> BackendResult:
        callbacks = RealtimeCallbacks(
            set_state=context.set_led_state,
            start_listen=context.anim_listen_start,
            stop_listen=context.anim_listen_stop,
            start_think=context.anim_think_start,
            stop_think=context.anim_think_stop,
            start_talk=context.anim_talk_start,
            stop_talk=context.anim_talk_stop,
            transcript=lambda text: context.logger.info("[openai-realtime] transcript: %s", text),
            local_command=lambda text: _route_local_command(text, context),
            logger=context.logger,
        )
        session = RealtimeSession(self.config, context.recorder, callbacks, shutdown_event=context.shutdown_event)
        try:
            session.start()
            session.wait_until_done(_followup_timeout_seconds())
            return BackendResult(handled=True)
        except Exception as exc:
            context.logger.error("[openai-realtime] Session failed: %s", exc)
            session.close()
            return BackendResult(
                handled=False,
                fallback_to_local=self.config.fallback_local,
                reason=str(exc),
            )


def _route_local_command(text: str, context: BackendContext) -> bool:
    if context.is_program_exit_command(text):
        context.say(context.model_byebye, recorder=context.recorder, wakeword=context.wakeword)
        context.shutdown_event.set()
        return True
    norm = context.normalize_command_text(text)
    if norm in {"danke", "stop", "nein danke", "tschuess", "tschuss", "byebye"}:
        context.say(context.eoc_ack, recorder=context.recorder, wakeword=context.wakeword)
        return True
    if context.is_email_request(text):
        context.handle_email_request(text, context.recorder, context.wakeword)
        return True
    return False


def _followup_timeout_seconds() -> float | None:
    if not _env_bool(os.environ, "FOLLOWUP_ENABLE", True):
        return 0.0
    try:
        return float(os.getenv("FOLLOWUP_ARM_S", "3.0"))
    except ValueError:
        return 3.0
