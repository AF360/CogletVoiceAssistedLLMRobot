import base64
import queue
import threading

import numpy as np
import pytest

from voice_backends.openai_realtime import (
    OpenAIRealtimeConfig,
    RealtimeCallbacks,
    RealtimeConfigError,
    RealtimeSession,
    build_session_update,
    clear_queue,
    load_instructions,
    pcm16_resample,
    websocket_headers,
    websocket_url,
)


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, *args):
        self.messages.append(("info", args))

    def warning(self, *args):
        self.messages.append(("warning", args))

    def error(self, *args):
        self.messages.append(("error", args))

    def debug(self, *args):
        self.messages.append(("debug", args))


class Recorder:
    sr = 16000

    def read_bytes(self, n):
        return (np.zeros(n // 2, dtype="<i2")).tobytes()


class FakeWs:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def recv(self):
        return ""

    def close(self):
        self.closed = True


def callbacks():
    events = []
    log = FakeLogger()
    return events, RealtimeCallbacks(
        set_state=lambda state: events.append(("state", state)),
        start_listen=lambda: events.append(("listen", "start")),
        stop_listen=lambda: events.append(("listen", "stop")),
        start_think=lambda: events.append(("think", "start")),
        stop_think=lambda: events.append(("think", "stop")),
        start_talk=lambda: events.append(("talk", "start")),
        stop_talk=lambda: events.append(("talk", "stop")),
        transcript=lambda text: events.append(("transcript", text)),
        local_command=lambda text: text == "stop",
        logger=log,
    )


def config(**overrides):
    values = {
        "api_key": "test-api-key",
        "instructions": "You are Coglet.",
    }
    values.update(overrides)
    return OpenAIRealtimeConfig(**values)


def test_missing_api_key_rejected():
    with pytest.raises(RealtimeConfigError):
        OpenAIRealtimeConfig.from_env({})


def test_load_instructions_de_prompt_from_coglet_lang():
    text = load_instructions({"COGLET_LANG": "de"})
    assert "Du bist Coglet" in text
    assert "Sprich immer auf Deutsch" in text


def test_load_instructions_en_prompt_from_coglet_lang():
    text = load_instructions({"COGLET_LANG": "en"})
    assert "You are Coglet" in text
    assert "Always speak English" in text


def test_inline_realtime_instructions_override_language_prompt():
    text = load_instructions({
        "COGLET_LANG": "en",
        "OPENAI_REALTIME_INSTRUCTIONS": "inline override",
    })
    assert text == "inline override"


def test_realtime_instructions_file_override_language_prompt(tmp_path):
    prompt_file = tmp_path / "custom-prompt.txt"
    prompt_file.write_text("file override", encoding="utf-8")
    text = load_instructions({
        "COGLET_LANG": "de",
        "OPENAI_REALTIME_INSTRUCTIONS_FILE": str(prompt_file),
    })
    assert text == "file override"

def test_ga_websocket_headers_do_not_include_beta_header():
    headers = websocket_headers(config(safety_identifier="hashed-local-id"))
    assert "Authorization: Bearer test-api-key" in headers
    assert "OpenAI-Safety-Identifier: hashed-local-id" in headers
    assert all("OpenAI-Beta" not in header for header in headers)


def test_websocket_url_contains_model():
    assert websocket_url(config(model="gpt-realtime-2")).endswith("?model=gpt-realtime-2")


def test_session_payload_server_vad_model_voice_reasoning():
    payload = build_session_update(config(voice="verse", reasoning_effort="low"))
    session = payload["session"]
    assert payload["type"] == "session.update"
    assert session["type"] == "realtime"
    assert session["reasoning"]["effort"] == "low"
    assert session["audio"]["output"]["voice"] == "verse"
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "server_vad",
        "create_response": True,
        "interrupt_response": True,
    }


def test_session_payload_semantic_vad():
    payload = build_session_update(config(vad_mode="semantic_vad", vad_eagerness="auto"))
    turn_detection = payload["session"]["audio"]["input"]["turn_detection"]
    assert turn_detection["type"] == "semantic_vad"
    assert turn_detection["eagerness"] == "auto"


def test_audio_input_conversion_resamples_pcm16():
    src = (np.sin(np.linspace(0, 1, 160, endpoint=False)) * 10000).astype("<i2").tobytes()
    out = pcm16_resample(src, 16000, 24000)
    assert len(out) > len(src)
    assert len(out) % 2 == 0


def test_output_audio_queue_and_state_transition_on_first_delta():
    events, cb = callbacks()
    session = RealtimeSession(
        config(),
        Recorder(),
        cb,
        websocket_factory=lambda *a, **k: FakeWs(),
    )
    payload = base64.b64encode(b"\x01\x00\x02\x00").decode("ascii")
    session.handle_event(
        {
            "type": "response.output_audio.delta",
            "delta": payload,
            "item_id": "itm_1",
        }
    )
    assert ("state", "speaking") in events
    assert ("talk", "start") in events
    assert session.output_queue.get_nowait() == b"\x01\x00\x02\x00"
    assert session.current_item_id == "itm_1"


def test_state_transition_on_speech_start_and_stop():
    events, cb = callbacks()
    session = RealtimeSession(
        config(),
        Recorder(),
        cb,
        websocket_factory=lambda *a, **k: FakeWs(),
    )
    session.handle_event({"type": "input_audio_buffer.speech_started"})
    session.handle_event({"type": "input_audio_buffer.speech_stopped"})
    assert ("state", "listening") in events
    assert ("state", "thinking") in events


def test_response_completion_goes_to_followup():
    events, cb = callbacks()
    session = RealtimeSession(
        config(),
        Recorder(),
        cb,
        websocket_factory=lambda *a, **k: FakeWs(),
    )
    session.talking = True
    session.handle_event({"type": "response.done", "response": {"status": "completed"}})
    assert ("talk", "stop") in events
    assert ("state", "await_followup") in events


def test_barge_in_clears_queue_and_sends_cancel_and_truncate():
    events, cb = callbacks()
    ws = FakeWs()
    session = RealtimeSession(
        config(),
        Recorder(),
        cb,
        websocket_factory=lambda *a, **k: ws,
    )
    session.ws = ws
    session.current_item_id = "item_123"
    session.talking = True
    session.output_queue.put_nowait(b"stale")
    session.interrupt()
    assert session.output_queue.empty()
    assert session.audio_interrupt_event.is_set()
    sent = "\n".join(ws.sent)
    assert "response.cancel" in sent
    assert "conversation.item.truncate" in sent
    assert ("state", "listening") in events


def test_malformed_json_event_is_ignored_by_receive_loop():
    events, cb = callbacks()
    session = RealtimeSession(
        config(),
        Recorder(),
        cb,
        websocket_factory=lambda *a, **k: FakeWs(),
    )
    session.handle_event({"type": "error", "error": {"message": "bad"}})
    assert session.stop_event.is_set()
