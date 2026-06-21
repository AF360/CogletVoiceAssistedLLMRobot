from __future__ import annotations

import sys
import types


usb_module = types.ModuleType("usb")
usb_core = types.ModuleType("usb.core")
usb_util = types.ModuleType("usb.util")
usb_core.find = lambda **kwargs: None
usb_util.dispose_resources = lambda dev: None
usb_util.CTRL_IN = 0x80
usb_util.CTRL_TYPE_VENDOR = 0x40
usb_util.CTRL_RECIPIENT_DEVICE = 0
usb_module.core = usb_core
usb_module.util = usb_util
sys.modules.setdefault("usb", usb_module)
sys.modules.setdefault("usb.core", usb_core)
sys.modules.setdefault("usb.util", usb_util)

from hardware.xvf_mic import ReSpeakerMic


def test_xvf_vad_requires_consecutive_speech_frames(monkeypatch):
    monkeypatch.setenv("XVF_VAD_START_FRAMES", "3")

    mic = ReSpeakerMic(debounce_frames=1)

    mic._apply_vad_sample(42, True)
    mic._apply_vad_sample(42, True)
    assert mic.get_status() == (False, 42)

    mic._apply_vad_sample(42, True)
    assert mic.get_status() == (True, 42)

    mic._apply_vad_sample(42, False)
    assert mic.get_status() == (True, 42)

    mic._apply_vad_sample(42, False)
    assert mic.get_status() == (False, 42)
