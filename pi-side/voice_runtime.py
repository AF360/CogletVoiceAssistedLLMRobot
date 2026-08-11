"""Hardware-minimal runtime facade for :mod:`local_mode`.

The regular launcher uses ``robot_runtime`` for servos, face tracking, DOA and
animations.  ``coglet-voice.py`` installs this module under that name instead.
Only the optional status LED has a real implementation; all robot operations
are deliberate no-ops.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class CogletState(str, Enum):
    AWAIT_WAKEWORD = "await_wakeword"
    AWAIT_FOLLOWUP = "await_followup"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    OFF = "off"


# The generic table setup intentionally does not expose XVF3800 hardware VAD
# or DOA.  local_mode automatically falls back to OpenWakeWord when they are
# unavailable.
ReSpeakerMic = None
XVF_MIC_AVAILABLE = False
_XVF_MIC_AVAILABLE = False

_status_led: Any = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def initialize_status_led() -> None:
    """Initialise the optional NeoPixel only when explicitly enabled."""
    global _status_led
    if not _env_bool("ENABLE_LED", False):
        logger.info("[voice] Status LED disabled (ENABLE_LED=0)")
        return

    try:
        from hardware.status_led import StatusLED

        _status_led = StatusLED(enabled=True)
        led_set_state_safe(CogletState.AWAIT_WAKEWORD)
        logger.info("[voice] Status LED initialised")
    except Exception as exc:
        _status_led = None
        logger.warning("[voice] Status LED unavailable: %s", exc)


def led_set_state_safe(state: CogletState | str) -> None:
    if _status_led is None:
        return
    try:
        _status_led.set_state(state)
    except Exception as exc:
        logger.debug("[voice] Status LED update failed: %s", exc)


def set_deep_sleep_led_pulse(phase: float) -> None:
    if _status_led is None or not hasattr(_status_led, "_set_rgb"):
        return
    level = (max(-1.0, min(1.0, phase)) + 1.0) / 2.0
    brightness = 0.02 + level * 0.15
    try:
        _status_led._set_rgb(int(255 * brightness), int(180 * brightness), 0)
    except Exception as exc:
        logger.debug("[voice] Status LED pulse failed: %s", exc)


def anim_listen_start() -> None:
    logger.info("[voice] listen_start")
    led_set_state_safe(CogletState.LISTENING)


def anim_listen_stop() -> None:
    logger.info("[voice] listen_stop")


def anim_think_start() -> None:
    logger.info("[voice] think_start")
    led_set_state_safe(CogletState.THINKING)


def anim_think_stop() -> None:
    logger.info("[voice] think_stop")


def anim_talk_start() -> None:
    logger.info("[voice] talk_start")
    led_set_state_safe(CogletState.SPEAKING)


def anim_talk_stop() -> None:
    logger.info("[voice] talk_stop")


def anim_error(msg: str = "") -> None:
    logger.error("[voice] error %s", msg)


def initialize_all_servos(_logger: logging.Logger | None) -> None:
    logger.info("[voice] Robot hardware disabled")
    return None


def setup_face_tracking(
    _logger: logging.Logger | None, _servo_setup: None
) -> None:
    return None


def get_anim_servo(_name: str) -> None:
    return None


def start_idle_animation() -> None:
    return None


def stop_idle_animation() -> None:
    return None


def eyelids_set_mode(_mode: str) -> None:
    return None


def apply_personality_neutral_pose() -> None:
    return None


def restore_neutral_pose_and_close_lid() -> None:
    return None


def cleanup_servo_hardware(_servo_setup: None) -> None:
    return None


def demomode() -> None:
    logger.warning("[voice] DEMOMODE has no voice-only action; exiting")
