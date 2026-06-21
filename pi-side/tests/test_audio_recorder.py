from __future__ import annotations

import sys
import types

import numpy as np

sys.modules.setdefault("sounddevice", types.SimpleNamespace(RawInputStream=object))
sys.modules.setdefault("openwakeword", types.SimpleNamespace(Model=object))

import hardware.audio as audio
from hardware.audio import Recorder, Wakeword


class FakeWakeModel:
    instances = []

    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.predict_lengths = []
        self.reset_count = 0
        FakeWakeModel.instances.append(self)

    def predict(self, pcm):
        self.predict_lengths.append(len(pcm))
        return {"wake": 0.0}

    def reset(self):
        self.reset_count += 1


def test_raw_input_callback_extracts_selected_channel(monkeypatch):
    monkeypatch.setenv("MIC_CHANNELS", "2")
    monkeypatch.setenv("MIC_CHANNEL_INDEX", "1")

    recorder = Recorder(sr=16000, vad_aggr=2)
    interleaved = np.array(
        [
            [100, 1000],
            [200, 2000],
            [300, 3000],
        ],
        dtype="<i2",
    ).tobytes()

    recorder._callback(interleaved, 3, None, None)

    selected = np.frombuffer(recorder.read_bytes(6), dtype="<i2")
    assert selected.tolist() == [1000, 2000, 3000]


def test_raw_input_callback_clamps_channel_index(monkeypatch):
    monkeypatch.setenv("MIC_CHANNELS", "2")
    monkeypatch.setenv("MIC_CHANNEL_INDEX", "9")

    recorder = Recorder(sr=16000, vad_aggr=2)
    interleaved = np.array(
        [
            [100, 1000],
            [200, 2000],
        ],
        dtype="<i2",
    ).tobytes()

    recorder._callback(interleaved, 2, None, None)

    selected = np.frombuffer(recorder.read_bytes(4), dtype="<i2")
    assert selected.tolist() == [1000, 2000]


def test_trim_buffer_keeps_recent_audio(monkeypatch):
    monkeypatch.setenv("MIC_CHANNELS", "1")

    recorder = Recorder(sr=10, vad_aggr=2)
    recorder._resid = np.array([1, 2], dtype="<i2").tobytes()
    recorder._q.put(np.array([3, 4], dtype="<i2").tobytes())
    recorder._q.put(np.array([5, 6], dtype="<i2").tobytes())

    recorder.trim_buffer(0.3)

    kept = np.frombuffer(recorder.read_bytes(6), dtype="<i2")
    assert kept.tolist() == [4, 5, 6]


def test_wakeword_stream_mode_feeds_1280_sample_frames(monkeypatch):
    FakeWakeModel.instances.clear()
    monkeypatch.setattr(audio, "_OWWModel", FakeWakeModel)
    monkeypatch.setenv("OWW_PREDICT_MODE", "stream")
    monkeypatch.delenv("OWW_HOP_MS", raising=False)

    wakeword = Wakeword("oww", "/tmp/fake.onnx", 0.5, hw_sr=16000)
    score = wakeword._score_audio(np.ones(2560, dtype=np.float32))

    model = FakeWakeModel.instances[-1]
    assert score == 0.0
    assert model.reset_count == 1
    assert model.predict_lengths == [1280, 1280, 1280]
    assert wakeword.hop_hw == 1280


def test_wakeword_window_mode_keeps_legacy_window(monkeypatch):
    FakeWakeModel.instances.clear()
    monkeypatch.setattr(audio, "_OWWModel", FakeWakeModel)
    monkeypatch.setenv("OWW_PREDICT_MODE", "window")
    monkeypatch.delenv("OWW_HOP_MS", raising=False)

    wakeword = Wakeword("oww", "/tmp/fake.onnx", 0.5, hw_sr=16000)
    wakeword._score_audio(np.ones(2560, dtype=np.float32))

    model = FakeWakeModel.instances[-1]
    assert wakeword.hop_hw == 2560
    assert model.predict_lengths == [12800, 12800]
