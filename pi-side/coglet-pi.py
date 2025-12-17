#!/usr/bin/env python3
"""
Coglet Pi side (v1.0.4.5):
- Wakeword (openwakeword) -> recording (sounddevice + webrtcvad) -> /stt on PC
- /api/chat (Ollama, stream=true) -> sentence buffering -> Piper TTS (FIFO to warm server) -> aplay
- Half-duplex: mic is muted during TTS (blocked for estimated speech duration).
- Deep Sleep: Enters low power/servo mode after inactivity (no voice interaction).
- NEW: ReSpeaker Hardware VAD & DOA Integration (xvf_mic).
- RESTORED: Follow-Up Logic.
- FIXED: Barge-In self-interruption (flush).

ENV (see /etc/default/coglet-pi):
  STT_URL, OLLAMA_URL, OLLAMA_MODEL, LLM_KEEP_ALIVE
  MIC_SR, MIC_DEVICE
  WAKEWORD_BACKEND, OWW_MODEL, OWW_THRESHOLD
  PIPER_VOICE, PIPER_VOICE_JSON
  PIPER_FIFO (/run/piper/in.jsonl)
  TTS_WPM (e.g., 185), TTS_PUNCT_PAUSE_MS (e.g., 180)
"""

__version__ = "1.0.4.5"

import os
import sys
import io
import json
import random
import time
import queue
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
from math import gcd
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Mapping, Optional, Set
from scipy.signal import resample_poly

from startup_checks import (
    StartupCheckError,
    check_ollama_model,
    check_piper_mqtt_connectivity,
    check_stt_health,
)

# NEW: Email Sender
try:
    import email_sender
except ImportError:
    email_sender = None

# --- Hardware / Audio Imports ---
from hardware.pca9685_servo import Servo, ServoConfig
from hardware.servo_calibration import (
    ServoCalibration,
    load_servo_calibration,
    merge_config_with_calibration,
)
from hardware.channel_config import parse_channel_list, resolve_channel_list
from hardware.servo_presets import (
    POSE_CALIBRATE,
    POSE_REST,
    SERVO_LAYOUT_V1,
    PERSONALITY_SERVO_NAMES,
    apply_pose,
)
from hardware.eyelid_controller import EyelidController
from hardware.audio import Recorder, SpeechEndpoint, Wakeword, set_global_listen_state

# NEW: Hardware Microphone Class
try:
    from hardware.xvf_mic import ReSpeakerMic
    _XVF_MIC_AVAILABLE = True
except ImportError:
    _XVF_MIC_AVAILABLE = False


# --- Logging Setup ---
from logging_setup import get_logger, setup_logging

setup_logging()
logger = get_logger()

# --- Optional Status LED ---
_STATUS_LED_IMPORT_ERROR: Exception | None = None
_status_led_available = (
    importlib.util.find_spec("hardware.status_led") is not None
    and importlib.util.find_spec("neopixel") is not None
    and importlib.util.find_spec("board") is not None
)

if _status_led_available:
    from hardware.status_led import StatusLED, CogletState
else:
    class _DummyCogletState:
        AWAIT_WAKEWORD = "await_wakeword"
        AWAIT_FOLLOWUP = "await_followup"
        LISTENING = "listening"
        THINKING = "thinking"
        SPEAKING = "speaking"
        OFF = "off"

    StatusLED = None
    CogletState = _DummyCogletState
    _STATUS_LED_IMPORT_ERROR = ImportError(
        "Status LED dependencies unavailable (requires neopixel + board)"
    )


@dataclass(frozen=True)
class ServoInitBundle:
    pca: Any
    servo_map: Dict[str, Servo]
    servo_channel_map: Dict[str, int]
    calibration_map: Dict[int, ServoCalibration]
    tracking_eye_names: tuple[str, ...]
    tracking_wheel_names: tuple[str, ...]
    tracking_yaw_name: str | None
    tracking_pitch_name: str | None

# -------------------- Konfig per ENV --------------------
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
MODEL_CONFIRM    = os.getenv("MODEL_CONFIRM", "Ja?")
MODEL_READY      = os.getenv("MODEL_READY", "Alle Subsysteme bereit. Ich erwarte das Wähkwörd.")
MODEL_BYEBYE     = os.getenv("MODEL_BYEBYE", "Tschüssen!")
EOC_ACK          = os.getenv("EOC_ACK", "Alles klar. Ich warte aufs neue Wähkwörd.")
AMS_ACK          = os.getenv("AMS_ACK", "Ich warte nun wieder auf das Wähkwörd.")
DS_ACK           = os.getenv("DS_ACK", "Ich mache ein Nickerchen. Wecke mich mit dem Wähkwört.")
OWW_MODEL        = os.getenv("OWW_MODEL", "/opt/coglet-pi/.venv/lib/python3.13/site-packages/openwakeword/resources/models/wheatley.onnx")
OWW_THRESHOLD    = float(os.getenv("OWW_THRESHOLD", "0.35"))
OWW_DEBUG        = int(os.getenv("OWW_DEBUG", "0"))
MIC_SR           = int(os.getenv("MIC_SR", "16000"))
VAD_AGGR         = int(os.getenv("VAD_AGGRESSIVENESS", "2"))
WAKEWORD_BACKEND = os.getenv("WAKEWORD_BACKEND", "oww")
PIPER_VOICE      = os.getenv("PIPER_VOICE", "/opt/piper/voices/de_DE-thorsten-high.onnx")
PIPER_VOICE_JSON = os.getenv("PIPER_VOICE_JSON", "/opt/piper/voices/de_DE-thorsten-high.onnx.json")
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

FACE_TRACKING_ENABLED = _parse_bool(os.getenv("FACE_TRACKING_ENABLED"), True)
FACE_TRACKING_PATROL_INTERVAL_S = float(os.getenv("FACE_TRACKING_TIMEOUT_S", "30.0"))
DEEP_SLEEP_TIMEOUT_S = float(os.getenv("DEEP_SLEEP_TIMEOUT_S", "300.0"))

def _parse_float_env(name: str, default: float, *, logger: logging.Logger) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%r → using %s", name, value, default)
        return default

def _parse_int_env(name: str, default: int, *, logger: logging.Logger) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r → using %s", name, value, default)
        return default

def _create_servo_config(prefix: str, base: ServoConfig, pwm_freq: float, *, logger: logging.Logger) -> ServoConfig:
    def _float(suffix: str, current: float) -> float:
        env_name = f"{prefix}_{suffix}"
        value = os.getenv(env_name)
        if value is None:
            return current
        try:
            return float(value)
        except ValueError:
            logger.warning("Invalid float for %s=%r → using %s", env_name, value, current)
            return current

    invert = _parse_bool(os.getenv(f"{prefix}_INVERT"), base.invert)
    freq = _float("PWM_FREQ_HZ", pwm_freq)
    try:
        return ServoConfig(
            min_angle_deg=_float("MIN_ANGLE_DEG", base.min_angle_deg),
            max_angle_deg=_float("MAX_ANGLE_DEG", base.max_angle_deg),
            min_pulse_us=_float("MIN_PULSE_US", base.min_pulse_us),
            max_pulse_us=_float("MAX_PULSE_US", base.max_pulse_us),
            max_speed_deg_per_s=_float("MAX_SPEED_DEG_PER_S", base.max_speed_deg_per_s),
            max_accel_deg_per_s2=_float("MAX_ACCEL_DEG_PER_S2", base.max_accel_deg_per_s2),
            deadzone_deg=_float("DEADZONE_DEG", base.deadzone_deg),
            neutral_deg=_float("NEUTRAL_DEG", base.neutral_deg),
            invert=invert,
            pwm_frequency_hz=freq,
        )
    except Exception as exc:
        logger.error("Invalid servo configuration for %s: %s", prefix, exc)
        return ServoConfig(
            min_angle_deg=base.min_angle_deg,
            max_angle_deg=base.max_angle_deg,
            min_pulse_us=base.min_pulse_us,
            max_pulse_us=base.max_pulse_us,
            max_speed_deg_per_s=base.max_speed_deg_per_s,
            max_accel_deg_per_s2=base.max_accel_deg_per_s2,
            deadzone_deg=base.deadzone_deg,
            neutral_deg=base.neutral_deg,
            invert=base.invert,
            pwm_frequency_hz=pwm_freq,
        )

def _build_face_tracking_config(logger: logging.Logger) -> "FaceTrackingConfig":
    from hardware.face_tracker import FaceTrackingConfig

    base_cfg = FaceTrackingConfig()
    def _float(name: str, current: float) -> float:
        return _parse_float_env(name, current, logger=logger)

    return FaceTrackingConfig(
        frame_width=_float("FACE_TRACKING_FRAME_WIDTH", base_cfg.frame_width),
        frame_height=_float("FACE_TRACKING_FRAME_HEIGHT", base_cfg.frame_height),
        coordinates_are_center=_parse_bool(os.getenv("FACE_TRACKING_COORDINATES_CENTER"), base_cfg.coordinates_are_center),
        eye_deadzone_px=_float("FACE_TRACKING_EYE_DEADZONE_PX", base_cfg.eye_deadzone_px),
        yaw_deadzone_px=_float("FACE_TRACKING_YAW_DEADZONE_PX", base_cfg.yaw_deadzone_px),
        pitch_deadzone_px=_float("FACE_TRACKING_PITCH_DEADZONE_PX", base_cfg.pitch_deadzone_px),
        eye_gain_deg_per_px=_float("FACE_TRACKING_EYE_GAIN_DEG_PER_PX", base_cfg.eye_gain_deg_per_px),
        yaw_gain_deg_per_px=_float("FACE_TRACKING_YAW_GAIN_DEG_PER_PX", base_cfg.yaw_gain_deg_per_px),
        pitch_gain_deg_per_px=_float("FACE_TRACKING_PITCH_GAIN_DEG_PER_PX", base_cfg.pitch_gain_deg_per_px),
        eye_max_delta_deg=_float("FACE_TRACKING_EYE_MAX_DELTA_DEG", base_cfg.eye_max_delta_deg),
        yaw_max_delta_deg=_float("FACE_TRACKING_YAW_MAX_DELTA_DEG", base_cfg.yaw_max_delta_deg),
        pitch_max_delta_deg=_float("FACE_TRACKING_PITCH_MAX_DELTA_DEG", base_cfg.pitch_max_delta_deg),
        invoke_interval_s=_float("FACE_TRACKING_INVOKE_INTERVAL_S", base_cfg.invoke_interval_s),
        invoke_timeout_s=_float("FACE_TRACKING_INVOKE_TIMEOUT_S", base_cfg.invoke_timeout_s),
        update_interval_s=_float("FACE_TRACKING_UPDATE_INTERVAL_S", base_cfg.update_interval_s),
        neutral_timeout_s=_float("FACE_TRACKING_NEUTRAL_TIMEOUT_S", base_cfg.neutral_timeout_s),
        wheel_deadzone_deg=_float("FACE_TRACKING_WHEEL_DEADZONE_DEG", base_cfg.wheel_deadzone_deg),
        wheel_follow_delay_s=_float("FACE_TRACKING_WHEEL_FOLLOW_DELAY_S", base_cfg.wheel_follow_delay_s),
        wheel_input_min_deg=_float("FACE_TRACKING_WHEEL_INPUT_MIN_DEG", base_cfg.wheel_input_min_deg),
        wheel_input_max_deg=_float("FACE_TRACKING_WHEEL_INPUT_MAX_DEG", base_cfg.wheel_input_max_deg),
        wheel_output_min_deg=_float("FACE_TRACKING_WHEEL_OUTPUT_MIN_DEG", base_cfg.wheel_output_min_deg),
        wheel_output_max_deg=_float("FACE_TRACKING_WHEEL_OUTPUT_MAX_DEG", base_cfg.wheel_output_max_deg),
        wheel_power=_float("FACE_TRACKING_WHEEL_POWER", base_cfg.wheel_power),
        patrol_enabled=_parse_bool(os.getenv("FACE_TRACKING_PATROL_ENABLED"), base_cfg.patrol_enabled),
        patrol_interval_s=_float("FACE_TRACKING_PATROL_INTERVAL_S", base_cfg.patrol_interval_s),
    )


def _create_eyelid_controller(servos: Mapping[str, Servo], *, logger: logging.Logger) -> EyelidController | None:
    lid_servo = servos.get("LID")
    if lid_servo is None:
        return None

    layout_cfg = lid_servo.config
    open_angle = _parse_float_env("EYELID_OPEN_DEG", layout_cfg.neutral_deg, logger=logger)
    open_angle = max(layout_cfg.min_angle_deg, min(layout_cfg.max_angle_deg, open_angle))
    default_closed = max(layout_cfg.min_angle_deg, min(layout_cfg.max_angle_deg, open_angle - 60.0))
    closed_angle = _parse_float_env("EYELID_CLOSED_DEG", default_closed, logger=logger)
    closed_angle = max(layout_cfg.min_angle_deg, min(layout_cfg.max_angle_deg, closed_angle))
    sleep_fraction = _parse_float_env("EYELID_SLEEP_FRACTION", 0.7, logger=logger)
    sleep_fraction = max(0.0, min(1.0, sleep_fraction))
    blink_min = _parse_float_env("EYELID_BLINK_MIN_S", 3.0, logger=logger)
    blink_max = _parse_float_env("EYELID_BLINK_MAX_S", 7.0, logger=logger)
    blink_close = _parse_float_env("EYELID_BLINK_CLOSE_S", 0.06, logger=logger)
    blink_hold = _parse_float_env("EYELID_BLINK_HOLD_S", 0.04, logger=logger)
    blink_open = _parse_float_env("EYELID_BLINK_OPEN_S", 0.07, logger=logger)

    try:
        controller = EyelidController(
            lid_servo,
            open_angle_deg=open_angle,
            closed_angle_deg=closed_angle,
            sleep_fraction=sleep_fraction,
            blink_interval_min_s=blink_min,
            blink_interval_max_s=blink_max,
            blink_close_s=blink_close,
            blink_hold_s=blink_hold,
            blink_open_s=blink_open,
        )
    except Exception as exc:
        logger.error("Eyelid controller disabled: %s", exc)
        return None

    logger.info(
        "Eyelid controller initialised (open %.1f°, closed %.1f°, blink %.2f–%.2f s)",
        open_angle,
        closed_angle,
        blink_min,
        blink_max,
    )
    return controller


def _initialize_all_servos(logger: logging.Logger) -> ServoInitBundle | None:
    try:
        import board
        import busio
        from adafruit_pca9685 import PCA9685
    except Exception as exc: 
        logger.error("Servo init failed: PCA9685 dependencies missing: %s", exc)
        return None

    pwm_freq = _parse_float_env("FACE_TRACKING_PWM_FREQ_HZ", 50.0, logger=logger)

    layout = SERVO_LAYOUT_V1
    default_eye_channels = [layout["EYL"].channel, layout["EYR"].channel]
    eye_channels = resolve_channel_list(
        env_value=os.getenv("FACE_TRACKING_EYE_CHANNELS"),
        default=default_eye_channels,
        allow_empty=False,
        logger=logger,
        env_name="FACE_TRACKING_EYE_CHANNELS",
    )

    default_wheel_channels = [layout["LWH"].channel, layout["RWH"].channel]
    wheel_channels = resolve_channel_list(
        env_value=os.getenv("FACE_TRACKING_WHEEL_CHANNELS"),
        default=default_wheel_channels,
        allow_empty=True,
        logger=logger,
        env_name="FACE_TRACKING_WHEEL_CHANNELS",
    )

    yaw_text = os.getenv("FACE_TRACKING_YAW_CHANNEL", "").strip()
    pitch_text = os.getenv("FACE_TRACKING_PITCH_CHANNEL", str(layout["NPT"].channel)).strip()

    servo_channel_map: Dict[str, int] = {name: definition.channel for name, definition in layout.items()}

    def _resolve_eye_channel(env_name: str, fallback_index: int) -> int | None:
        value = os.getenv(env_name)
        if value:
            candidates = parse_channel_list(value, logger=logger)
            if candidates:
                return candidates[0]
            logger.warning("No valid PCA9685 channel in %s=%r", env_name, value)
        if fallback_index < len(eye_channels):
            return eye_channels[fallback_index]
        return None

    def _resolve_wheel_channel(env_name: str, fallback_index: int) -> int | None:
        value = os.getenv(env_name)
        if value:
            candidates = parse_channel_list(value, logger=logger)
            if candidates:
                return candidates[0]
            logger.warning("No valid PCA9685 channel in %s=%r", env_name, value)
        if fallback_index < len(wheel_channels):
            return wheel_channels[fallback_index]
        return None

    left_idx = _resolve_eye_channel("FACE_TRACKING_EYE_LEFT_CHANNEL", 0)
    if left_idx is not None:
        servo_channel_map["EYL"] = left_idx
    right_idx = _resolve_eye_channel("FACE_TRACKING_EYE_RIGHT_CHANNEL", 1)
    if right_idx is not None:
        servo_channel_map["EYR"] = right_idx

    yaw_enabled = False
    if yaw_text:
        yaw_candidates = parse_channel_list(yaw_text, logger=logger)
        if yaw_candidates:
            servo_channel_map["NRL"] = yaw_candidates[0]
            yaw_enabled = True
        else:
            logger.warning("No valid yaw channel in FACE_TRACKING_YAW_CHANNEL=%r", yaw_text)

    pitch_candidates = parse_channel_list(pitch_text, logger=logger) if pitch_text else []
    if pitch_candidates:
        servo_channel_map["NPT"] = pitch_candidates[0]
    elif pitch_text:
        logger.warning("No valid pitch channel in FACE_TRACKING_PITCH_CHANNEL=%r", pitch_text)

    wheel_left_idx = _resolve_wheel_channel("FACE_TRACKING_WHEEL_LEFT_CHANNEL", 0)
    if wheel_left_idx is not None:
        servo_channel_map["LWH"] = wheel_left_idx
    wheel_right_idx = _resolve_wheel_channel("FACE_TRACKING_WHEEL_RIGHT_CHANNEL", 1)
    if wheel_right_idx is not None:
        servo_channel_map["RWH"] = wheel_right_idx

    logger.info(
        "Servo init config → eyes=%s yaw=%s pitch=%s wheels=%s PWM=%.2fHz",
        [servo_channel_map.get(name) for name in ("EYL", "EYR")],
        servo_channel_map.get("NRL") if yaw_enabled else "disabled",
        servo_channel_map.get("NPT") if pitch_text else "disabled",
        wheel_channels or "disabled",
        pwm_freq,
    )

    tracking_eye_names: tuple[str, ...] = tuple(name for name in ("EYL", "EYR") if servo_channel_map.get(name) is not None)
    tracking_wheel_names: tuple[str, ...] = tuple(name for name in ("LWH", "RWH") if wheel_channels)
    tracking_yaw_name = "NRL" if yaw_enabled else None
    tracking_pitch_name = "NPT" if servo_channel_map.get("NPT") is not None else None

    calibration_map, calibration_path = load_servo_calibration(logger)
    if calibration_map:
        logger.info(
            "Using servo calibration from %s for %d channel(s)",
            calibration_path,
            len(calibration_map),
        )
    elif calibration_path is None:
        logger.info("No servo calibration file found; using preset servo layout SERVO_LAYOUT_V1")
    else:
        logger.info("Servo calibration file %s contained no usable entries; using presets", calibration_path)
    logger.info(
        "Servo start angles use clamped calibration start_deg when available; otherwise preset/env neutral_deg applies."
    )

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        pca = PCA9685(i2c)
        pca.frequency = max(1, int(round(pwm_freq)))
    except Exception as exc:
        logger.error("Servo init failed: PCA9685 init failed: %s", exc)
        return None

    servo_map: Dict[str, Servo] = {}
    start_angles: Dict[str, float] = {}

    prefix_map = {
        "EYL": "FACE_TRACKING_EYE",
        "EYR": "FACE_TRACKING_EYE",
        "NPT": "FACE_TRACKING_PITCH",
        "NRL": "FACE_TRACKING_YAW",
        "LWH": "FACE_TRACKING_WHEEL_LEFT",
        "RWH": "FACE_TRACKING_WHEEL_RIGHT",
    }

    for name, definition in layout.items():
        idx = servo_channel_map.get(name)
        if idx is None:
            logger.error("Servo init failed: no PCA9685 channel resolved for %s", name)
            try:
                pca.deinit()
            except Exception:
                pass
            return None
        prefix = prefix_map.get(name, f"ANIM_{name}")
        try:
            env_config = _create_servo_config(prefix, definition.config, pwm_freq, logger=logger)
            calibration = calibration_map.get(idx)
            config = merge_config_with_calibration(env_config, calibration)
            servo = Servo(pca.channels[idx], config=config)
        except Exception as exc:
            logger.error("Servo init failed for %s on channel %d: %s", name, idx, exc)
            try:
                pca.deinit()
            except Exception:
                pass
            return None
        servo_map[name] = servo
        start_angles[name] = servo.config.neutral_deg

    _register_shutdown_targets(servo_map, servo_channel_map, calibration_map)

    eyelids = _create_eyelid_controller(servo_map, logger=logger)
    _set_eyelids(eyelids)
    if eyelids is not None:
        try:
            eyelids.set_mode("auto")
        except Exception as exc:
            logger.debug("Setting eyelids to auto failed: %s", exc)

    anim_servos = {name: servo for name, servo in servo_map.items() if name in PERSONALITY_SERVO_NAMES}
    _register_anim_servos(anim_servos)

    for name, angle in sorted(start_angles.items()):
        channel = servo_channel_map.get(name)
        calib = calibration_map.get(channel or -1)
        source = "calibrated start_deg" if calib else "preset neutral"
        logger.info("Servo %s (ch %s) start angle: %.1f° (%s)", name, channel, angle, source)

    return ServoInitBundle(
        pca=pca,
        servo_map=servo_map,
        servo_channel_map=servo_channel_map,
        calibration_map=calibration_map,
        tracking_eye_names=tracking_eye_names,
        tracking_wheel_names=tracking_wheel_names,
        tracking_yaw_name=tracking_yaw_name,
        tracking_pitch_name=tracking_pitch_name,
    )


def _setup_face_tracking(
    logger: logging.Logger, servo_setup: ServoInitBundle | None
) -> tuple["FaceTracker", Callable[[], None]] | None:
    if not FACE_TRACKING_ENABLED:
        logger.info("Face tracking disabled via FACE_TRACKING_ENABLED=0")
        return None

    if servo_setup is None:
        logger.error("Face tracking disabled: servos not initialised")
        return None

    try:
        from hardware.face_tracker import FaceTracker, FaceTrackingServos
        from hardware.grove_vision_ai import GroveVisionAIClient
    except Exception as exc:
        logger.error("Face tracking disabled (missing dependencies): %s", exc)
        return None

    serial_port = os.getenv("FACE_TRACKING_SERIAL_PORT", "/dev/ttyACM0")
    baudrate = _parse_int_env("FACE_TRACKING_BAUDRATE", 921600, logger=logger)
    read_timeout = _parse_float_env("FACE_TRACKING_SERIAL_TIMEOUT", 0.0, logger=logger)

    servos = servo_setup.servo_map
    logger.info(
        "Face tracking servo selection → eyes=%s yaw=%s pitch=%s wheels=%s",
        servo_setup.tracking_eye_names,
        servo_setup.tracking_yaw_name or "disabled",
        servo_setup.tracking_pitch_name or "disabled",
        servo_setup.tracking_wheel_names or ("disabled",),
    )
    if not servo_setup.tracking_yaw_name:
        logger.info("Yaw servo disabled for face tracking; horizontal rotation relies on wheels.")
    if not servo_setup.tracking_wheel_names:
        logger.info("Wheel servos disabled for face tracking; horizontal rotation relies on eyes/yaw only.")
    eye_servos = tuple(servos[name] for name in servo_setup.tracking_eye_names if name in servos)
    if not eye_servos:
        logger.error("Face tracking disabled: no valid eye servos available")
        return None

    yaw_servo = servos.get(servo_setup.tracking_yaw_name) if servo_setup.tracking_yaw_name else None
    pitch_servo = servos.get(servo_setup.tracking_pitch_name) if servo_setup.tracking_pitch_name else None
    wheel_servos = tuple(
        servos[name]
        for name in servo_setup.tracking_wheel_names
        if name in servos
    )

    try:
        client = GroveVisionAIClient(serial_port, baudrate=baudrate, read_timeout=read_timeout)
    except Exception as exc:
        logger.error("Face tracking disabled: cannot open %s: %s", serial_port, exc)
        return None

    tracker_config = _build_face_tracking_config(logger)
    tracker_servos = FaceTrackingServos(
        eyes=eye_servos,
        yaw=yaw_servo,
        pitch=pitch_servo,
        wheels=wheel_servos,
    )
    tracker = FaceTracker(client, tracker_servos, config=tracker_config)

    def _cleanup() -> None:
        try:
            tracker.stop()
        except Exception as exc:
            logger.debug("Face tracker stop failed: %s", exc)
        try:
            client.close()
        except Exception as exc:
            logger.debug("Closing Grove Vision client failed: %s", exc)

    logger.info(
        "Face tracking initialised on %s (baud %d) with eyes=%s, yaw=%s, pitch=%s, wheels=%s",
        serial_port,
        baudrate,
        [servo_setup.servo_channel_map.get(name) for name in servo_setup.tracking_eye_names],
        servo_setup.servo_channel_map.get("NRL") if servo_setup.tracking_yaw_name else None,
        servo_setup.servo_channel_map.get("NPT") if servo_setup.tracking_pitch_name else None,
        [servo_setup.servo_channel_map.get(name) for name in servo_setup.tracking_wheel_names],
    )
    return tracker, _cleanup

# -------------------- Animation helpers --------------------
_anim_servos: Dict[str, Servo] = {}
_anim_lock = threading.Lock()
_eyelid_controller: EyelidController | None = None
_mouth_thread: threading.Thread | None = None
_mouth_stop_event = threading.Event()
_thinking_thread: threading.Thread | None = None
_thinking_stop_event = threading.Event()
_shutdown_event = threading.Event()
_shutdown_targets_lock = threading.Lock()
_shutdown_servos_by_channel: Dict[int, Servo] = {}
_shutdown_calibration: Dict[int, ServoCalibration] = {}

# Idle Animation Globals
_idle_thread: threading.Thread | None = None
_idle_stop_event = threading.Event()

def _register_anim_servos(servos: Mapping[str, Servo]) -> None:
    global _anim_servos
    with _anim_lock:
        _anim_servos = {
            name: servo
            for name, servo in servos.items()
            if name in PERSONALITY_SERVO_NAMES
        }

def _get_anim_servo(name: str) -> Servo | None:
    with _anim_lock:
        return _anim_servos.get(name)

def _get_eyelids() -> EyelidController | None:
    with _anim_lock:
        return _eyelid_controller

def _set_eyelids(controller: EyelidController | None) -> None:
    global _eyelid_controller
    with _anim_lock:
        _eyelid_controller = controller

def _register_shutdown_targets(
    servos: Mapping[str, Servo],
    channel_map: Mapping[str, int],
    calibration: Mapping[int, ServoCalibration],
) -> None:
    with _shutdown_targets_lock:
        _shutdown_servos_by_channel.clear()
        for name, servo in servos.items():
            channel = channel_map.get(name)
            if channel is None:
                continue
            _shutdown_servos_by_channel[channel] = servo
        _shutdown_calibration.clear()
        _shutdown_calibration.update(calibration)

def _shutdown_eyelids() -> None:
    controller = _get_eyelids()
    _set_eyelids(None)
    if controller is not None:
        try:
            controller.shutdown()
        except Exception as exc:
            logger.debug("Eyelid controller shutdown failed: %s", exc)

def _eyelids_set_mode(mode: str) -> None:
    controller = _get_eyelids()
    if controller is None:
        return
    try:
        controller.set_mode(mode)
    except Exception as exc:
        logger.debug("Setting eyelid mode failed: %s", exc)

def _eyelids_set_override(angle: float, *, duration_s: float) -> None:
    controller = _get_eyelids()
    if controller is None:
        return
    try:
        controller.set_override(angle, duration_s=duration_s)
    except Exception as exc:
        logger.debug("Setting eyelid override failed: %s", exc)

def _eyelids_override_fraction(fraction: float, *, duration_s: float) -> None:
    controller = _get_eyelids()
    if controller is None:
        return
    try:
        target = controller.angle_for_fraction(fraction)
    except Exception as exc:
        logger.debug("Calculating eyelid override angle failed: %s", exc)
        return
    _eyelids_set_override(target, duration_s=duration_s)

def _apply_pose_safe(pose: str | Mapping[str, float]) -> None:
    with _anim_lock:
        target_servos = dict(_anim_servos)
    if not target_servos:
        return
    try:
        apply_pose(target_servos, pose)
    except Exception as exc:
        logger.debug("Applying pose failed: %s", exc)

def _move_servos_to_stop_positions() -> None:
    with _shutdown_targets_lock:
        targets = list(_shutdown_servos_by_channel.items())
        calibration = dict(_shutdown_calibration)
    if not targets:
        return
    logger.info(
        "Shutting down, moving servos to park position (calibrated stop_deg when available, otherwise neutral)."
    )
    for channel, servo in targets:
        calib = calibration.get(channel)
        if calib and calib.stop_deg is not None:
            target = calib.clamped_stop
            reason = "calibrated stop_deg"
        elif calib:
            target = calib.clamped_stop
            reason = "calibrated neutral/start"
        else:
            target = servo.config.neutral_deg
            reason = "preset/env neutral"
        try:
            servo.move_to(target)
            logger.debug("Parking servo channel %d to %.1f° (%s)", channel, target, reason)
        except Exception as exc:
            logger.debug("Parking servo channel %d failed: %s", channel, exc)
    logger.info("Servos parked, exiting Coglet.")

def _restore_neutral_pose_and_close_lid() -> None:
    try:
        _move_servos_to_stop_positions()
    except Exception as exc:
        logger.debug("Servo parking during shutdown failed: %s", exc)

def _cleanup_servo_hardware(servo_setup: ServoInitBundle | None) -> None:
    if servo_setup is None:
        return
    try:
        _shutdown_eyelids()
    except Exception as exc:
        logger.debug("Eyelid controller cleanup failed: %s", exc)
    try:
        servo_setup.pca.deinit()
    except Exception as exc:
        logger.debug("PCA9685 cleanup failed: %s", exc)

def _led_set_state_safe(state) -> None:
    if _status_led is None or CogletState is None:
        return
    try:
        _status_led.set_state(state)
    except Exception as exc:
        logger.debug("Status LED update failed: %s", exc)

def _initialize_status_led() -> None:
    global _status_led
    if StatusLED is not None and CogletState is not None:
        try:
            _status_led = StatusLED()
            _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
        except Exception as exc:
            logger.warning("Status LED init failed: %s", exc)
            _status_led = None
    elif _STATUS_LED_IMPORT_ERROR is not None:
        logger.info("Status LED unavailable (import failed): %s", _STATUS_LED_IMPORT_ERROR)

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

def _mouth_loop(open_angle: float, close_angle: float, rest_angle: float) -> None:
    servo = _get_anim_servo("MOU")
    if servo is None:
        return
    toggle = False
    last = time.monotonic()
    servo.move_to(rest_angle)

    while not _mouth_stop_event.wait(0.0):
        now = time.monotonic()
        dt = now - last
        last = now

        target = open_angle if toggle else close_angle
        servo.move_to(target)

        steps = max(1, int(dt / 0.02))
        step_dt = dt / steps if steps > 0 else 0.02
        for _ in range(steps):
            servo.update(step_dt)

        if _mouth_stop_event.wait(0.25):
            break
        toggle = not toggle

    servo.move_to(rest_angle)
    for _ in range(3):
        servo.update(0.05)

def _start_mouth_animation() -> None:
    global _mouth_thread
    servo = _get_anim_servo("MOU")
    if servo is None:
        return
    with _anim_lock:
        if _mouth_thread and _mouth_thread.is_alive():
            return
        _mouth_stop_event.clear()
        rest = servo.config.neutral_deg
        open_angle = max(servo.config.min_angle_deg, min(servo.config.max_angle_deg, rest - 40.0))
        close_angle = min(servo.config.max_angle_deg, max(servo.config.min_angle_deg, rest - 10.0))
        _mouth_thread = threading.Thread(
            target=_mouth_loop,
            args=(open_angle, close_angle, rest),
            name="MouthAnimation",
            daemon=True,
        )
        _mouth_thread.start()

def _stop_mouth_animation() -> None:
    global _mouth_thread
    with _anim_lock:
        thread = _mouth_thread
        _mouth_thread = None
        _mouth_stop_event.set()
    if thread is not None:
        thread.join(timeout=1.0)
    servo = _get_anim_servo("MOU")
    if servo is not None:
        servo.move_to(servo.config.neutral_deg)

def _start_thinking_animation() -> None:
    global _thinking_thread
    with _anim_lock:
        if _thinking_thread and _thinking_thread.is_alive():
            return
        _thinking_stop_event.clear()
        _thinking_thread = threading.Thread(
            target=_thinking_loop,
            args=(_thinking_stop_event,),
            name="ThinkingAnimation",
            daemon=True,
        )
        _thinking_thread.start()

def _stop_thinking_animation() -> None:
    global _thinking_thread
    with _anim_lock:
        thread = _thinking_thread
        _thinking_thread = None
        _thinking_stop_event.set()
    if thread is not None:
        thread.join(timeout=1.0)

def _thinking_loop(stop_event: threading.Event) -> None:
    head = _get_anim_servo("NRL")
    left_ear = _get_anim_servo("EAL")
    right_ear = _get_anim_servo("EAR")

    if head is None and left_ear is None and right_ear is None:
        logger.debug("Thinking animation skipped (no servos available)")
        stop_event.wait()
        return

    head_neutral = head.config.neutral_deg if head is not None else 0.0
    head_left = _clamp_servo_angle(head, head_neutral + 20.0) if head is not None else None
    head_right = _clamp_servo_angle(head, head_neutral - 20.0) if head is not None else None

    left_forward = (
        _clamp_servo_angle(left_ear, left_ear.config.neutral_deg + 25.0)
        if left_ear is not None
        else None
    )
    left_back = (
        _clamp_servo_angle(left_ear, left_ear.config.neutral_deg - 25.0)
        if left_ear is not None
        else None
    )

    right_forward = (
        _clamp_servo_angle(right_ear, right_ear.config.neutral_deg + 25.0)
        if right_ear is not None
        else None
    )
    right_back = (
        _clamp_servo_angle(right_ear, right_ear.config.neutral_deg - 25.0)
        if right_ear is not None
        else None
    )

    toggle = False
    while not stop_event.is_set():
        targets: Dict[str, float] = {}
        if head_left is not None and head_right is not None:
            targets["NRL"] = head_left if toggle else head_right
        if left_forward is not None and left_back is not None:
            targets["EAL"] = left_forward if toggle else left_back
        if right_forward is not None and right_back is not None:
            targets["EAR"] = right_back if toggle else right_forward

        _eyelids_override_fraction(0.5, duration_s=2.0)
        _drive_anim_targets(targets, 0.9, stop_event)
        if stop_event.is_set():
            break
        _drive_anim_targets(targets, 0.7, stop_event)
        toggle = not toggle

# Idle Animation Loop
def _idle_loop(stop_event: threading.Event) -> None:
    stop_event.wait(random.uniform(2.0, 5.0))
    while not stop_event.is_set():
        if stop_event.wait(random.uniform(5.0, 10.0)):
            break
        actions = []
        if _get_anim_servo("EAL"): actions.append("EAL")
        if _get_anim_servo("EAR"): actions.append("EAR")
        if _get_anim_servo("NRL"): actions.append("NRL")
        
        if not actions:
            stop_event.wait(5.0)
            continue
            
        servo_name = random.choice(actions)
        servo = _get_anim_servo(servo_name)
        if servo:
            neutral = servo.config.neutral_deg
            offset = random.choice([-12.0, 12.0])
            target = _clamp_servo_angle(servo, neutral + offset)
            servo.move_to(target)
            time.sleep(0.3)
            servo.move_to(neutral)

def _start_idle_animation() -> None:
    global _idle_thread
    with _anim_lock:
        if _idle_thread and _idle_thread.is_alive():
            return
        _idle_stop_event.clear()
        _idle_thread = threading.Thread(
            target=_idle_loop,
            args=(_idle_stop_event,),
            name="IdleAnimation",
            daemon=True,
        )
        _idle_thread.start()

def _stop_idle_animation() -> None:
    global _idle_thread
    with _anim_lock:
        thread = _idle_thread
        _idle_thread = None
        _idle_stop_event.set()
    if thread is not None:
        thread.join(timeout=1.0)
    _apply_pose_safe(_personality_neutral_targets())

def anim_listen_start():
    logger.info("[anim] listen_start")
    _eyelids_set_mode("auto")
    _apply_pose_safe("pose_curious_2")
    _eyelids_override_fraction(0.0, duration_s=2.0)
    _led_set_state_safe(CogletState.LISTENING)

def anim_listen_stop():
    logger.info("[anim] listen_stop")
    _eyelids_set_mode("auto")
    _apply_pose_safe(_personality_neutral_targets())

def anim_think_start():
    logger.info("[anim] think_start")
    _eyelids_set_mode("auto")
    _start_thinking_animation()
    _led_set_state_safe(CogletState.THINKING)

def anim_think_stop():
    logger.info("[anim] think_stop")
    _stop_thinking_animation()
    _eyelids_set_mode("auto")

def anim_talk_start():
    logger.info("[anim] talk_start")
    # WICHTIG: Erst Denken stoppen, dann Sprechen starten
    _stop_thinking_animation() 
    _eyelids_set_mode("auto")
    _start_mouth_animation()
    _led_set_state_safe(CogletState.SPEAKING)

def anim_talk_stop():
    logger.info("[anim] talk_stop")
    _eyelids_set_mode("auto")
    _stop_mouth_animation()

def anim_error(msg=""):
    logger.error("[anim] error %s", msg)
    _eyelids_set_mode("closed")

_tts_active = False
_status_led: Any = None
sd.default.samplerate = MIC_SR

def _normalize_command_text(text: str) -> str:
    normalized = (text or "").lower()
    normalized = re.sub(r"[^\wäöüß\s-]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()

def _is_program_exit_command(text: str) -> bool:
    normalized = _normalize_command_text(text)
    if not normalized:
        return False
    collapsed = normalized.replace(" ", "")
    exits = {"programm ende", "programmende", "programm-ende"}
    return normalized in exits or collapsed in exits


def _is_email_request(text: str) -> bool:
    normalized = _normalize_command_text(text)
    
    # NEW: Bilingual support (DE/EN) & tolerant regex patterns
    # 1. Pattern: Prepositions (via, per, by)
    # DE: "per Mail", "als E-Mail", "via Nachricht"
    # EN: "by mail", "via email", "as an email"
    # Searching for: Preposition + (optional Article/Prep) + Object
    if re.search(r"\b(per|als|via|by|as|in)\s+(an\s+|a\s+|an\s+)?(e-?mail|mail|nachricht|message)\b", normalized):
        logger.info(f"[intent] Detected Email intent (Preposition): {text}")
        return True

    # 2. Pattern: Explicit Verb "mail" / "email"
    # DE: "maile mir", "email an uns"
    # EN: "email me", "mail it to"
    # Suffixes: DE (e, en, st, t), EN (s, ing, ed) or empty
    if re.search(r"\b(e-?mail|mail)(e|en|st|t|s|ing|ed)?\s+(mir|uns|an|me|us|to)\b", normalized):
        logger.info(f"[intent] Detected Email intent (Verb): {text}")
        return True

    # 3. Pattern: Classic Verb + Object (Keywords)
    words = set(normalized.split())
    strong_verbs = {
        # DE
        "schick", "schicke", "schicken", "schickt", 
        "sende", "senden", "sendet", 
        "versende", "versenden", "verschicken", "verschickt",
        "schreib", "schreibe", "erstell", "erstelle",
        # EN
        "send", "sends", "sending", "sent",
        "write", "writing", 
        "compose", "create", 
        "forward", "dispatch"
    }
    objects = {
        # DE
        "mail", "email", "e-mail", "e-mails", "emails", "nachricht",
        # EN
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
        speak_and_back_to_idle("Keine Empfängeradresse konfiguriert.", rec, kw)
        return True

    logger.info("[email] Handling request: %r", user_text)

    # 1. System Prompt: Force a "Professional Editor" persona.
    system_prompt = (
        "Du bist ein professioneller, freundlicher Redakteur. "
        "Deine Aufgabe ist es, ausführliche, hilfreiche und schön formatierte E-Mails zu schreiben. "
        "Nutze HTML zur Strukturierung: <h2> für Überschriften, <ul>/<li> für Listen, <b> für Wichtiges und <p> für Absätze. "
        "Nutze Emojis 🌟, wo es passend ist. "
        "Antworte NICHT kurz, sondern detailliert und vollständig."
    )

    # 2. User Prompt
    user_prompt = (
        f"Benutzeranfrage: '{user_text}'.\n\n"
        "Generiere eine E-Mail mit:\n"
        "1. Einem passenden Betreff (Subject: ...)\n"
        "2. Einem Trenner (---)\n"
        "3. Dem ausführlichen HTML-Inhalt (ohne <html>/<body> Tags, nur der Content).\n"
        "Beispiel-Format:\n"
        "Subject: Leckeres Rezept für Dich 🥗\n"
        "---\n"
        "<h2>Hier ist dein Rezept</h2><p>...</p>"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    anim_think_start()
    try:
        # Request generation with high temperature (creativity) AND high token limit (length)
        # num_predict=2048 allows for very long recipes.
        response = _ollama_chat(messages, temperature=0.6, num_predict=4096, timeout=600.0)
    except Exception as e:
        logger.error("[email] LLM generation error: %s", e)
        anim_think_stop()
        speak_and_back_to_idle("Fehler bei der Generierung.", rec, kw)
        return True

    anim_think_stop()

    # Parse the response (Header vs Body)
    subject = "Info von Wheatley"
    body = response

    if "Subject:" in response and "---" in response:
        try:
            parts = response.split("---", 1)
            header_part = parts[0].strip()
            body_part = parts[1].strip()

            # Extract Subject
            m = re.search(r"Subject:\s*(.*)", header_part, re.IGNORECASE)
            if m:
                subject = m.group(1).strip()

            body = body_part
        except Exception as e:
            logger.warning("[email] Parsing failed, sending raw content: %s", e)
    
    # Fallback subject enhancement
    if subject == "Info von Wheatley":
        subject = f"Info zu: {user_text[:20]}..."

    # Send the email
    try:
        email_sender.send_email_smtp(email_to, subject, body)
        logger.info("[email] Sent successfully to %s", email_to)
        speak_and_back_to_idle("Alles klar, ich habe dir eine ausführliche E-Mail geschickt und warte nun wieder auf das Wähkwört", rec, kw)
    except Exception as e:
        logger.error("[email] SMTP sending failed: %s", e)
        speak_and_back_to_idle("Ich konnte die E-Mail leider nicht senden.", rec, kw)

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

# -------------------- Piper MQTT helpers --------------------
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
                
                # A. WARTEN AUF AUDIO-START (Generation dauert!)
                # Wir geben ihm bis zu 5 Sekunden Zeit zum Rechnen (für Thorsten High)
                # Das ist kein "Sleep", sondern ein Timeout für die Schleife.
                deadline = time.time() + 5.0 
                
                while time.time() < deadline:
                    state = _tts_states.get(tts_id)
                    # HIER IST DER FIX: Wir ignorieren "START".
                    # Wir brechen erst aus, wenn er wirklich spricht ("SPEAKING") 
                    # oder wenn es vorbei/kaputt ist.
                    if state in {"SPEAKING", "DONE", "CANCELLED", "ERROR"}:
                        break
                    time.sleep(0.05)
                
                # B. JETZT MUND BEWEGEN
                # Entweder weil "SPEAKING" kam, oder weil Timeout abgelaufen ist (Fallback)
                _ensure_talk_anim_started(tts_id)
                _tts_manual_started.add(tts_id)

                # C. BARGE-IN SCHLEIFE (Während er spricht)
                if BARGE_IN and recorder and wakeword:
                    # Puffer und Engine leeren gegen Selbst-Unterbrechung
                    _flush_input_buffers(recorder)
                    if hasattr(wakeword, "reset"):
                        wakeword.reset()
                    
                    # Wie lange warten wir maximal aufs Ende des Sprechens?
                    start_wait = time.time()
                    timeout_sec = max(6.0, est * 2 + 2.0)
                    
                    while time.time() < start_wait + timeout_sec:
                        ev = _tts_events.get(tts_id)
                        # Wenn fertig (DONE/CANCELLED/ERROR) -> Raus
                        if ev and ev.is_set():
                            break
                        
                        # Barge-In Check
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
                    # Fallback ohne Barge-In: Einfach warten bis fertig
                    _wait_for_tts_done(tts_id, fallback_seconds=est, hard_timeout=max(6.0, est * 2 + 2.0))
                
                time.sleep(0.1)
                if _tts_states.get(tts_id) not in {"DONE", "CANCELLED", "ERROR"}:
                    anim_talk_stop()
                _clear_tts_tracking(tts_id)

        if used:
            return

        # Fallback (FIFO / Shell)
        anim_talk_start()
        if _fifo_write_nonblock(PIPER_FIFO, line):
            if not BARGE_IN:
                time.sleep(estimate_tts_seconds(text))
            anim_talk_stop()
            return
        anim_talk_stop()

        # Fallback (One-Shot)
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

def speak_and_back_to_idle(text: str, recorder, kw):
    if not BARGE_IN:
        set_global_listen_state(False)
        recorder.set_listen(False)
        recorder.flush()

    try:
        say(text, recorder=recorder, wakeword=kw)
    finally:
        if not BARGE_IN:
            set_global_listen_state(False)
            # Keep input fully muted during the cooldown to avoid TTS tail pickup.
            post_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5"))
            if post_cd > 0:
                time.sleep(post_cd)

            # Flush any queued frames and re-arm wakeword after suppression window.
            _flush_input_buffers(recorder)
            kw.reset_after_tts()
            set_global_listen_state(True)
            recorder.set_listen(True)
        else:
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

def _clamp_servo_angle(servo: Servo, angle: float) -> float:
    cfg = servo.config
    return max(cfg.min_angle_deg, min(cfg.max_angle_deg, angle))

def _drive_anim_targets(targets: Mapping[str, float], duration_s: float, stop_event: threading.Event) -> None:
    servo_targets: Dict[Servo, float] = {}
    for name, angle in targets.items():
        servo = _get_anim_servo(name)
        if servo is None: continue
        servo_targets[servo] = _clamp_servo_angle(servo, angle)
    if not servo_targets:
        stop_event.wait(duration_s)
        return
    for servo, angle in servo_targets.items():
        servo.move_to(angle)
    remaining = max(0.0, duration_s)
    step = 0.05
    while remaining > 0.0:
        dt = min(step, remaining)
        if stop_event.wait(dt): break
        for servo in servo_targets: servo.update(dt)
        remaining -= dt

def _personality_neutral_targets() -> Dict[str, float]:
    neutral_targets: Dict[str, float] = {}
    for name in PERSONALITY_SERVO_NAMES:
        servo = _get_anim_servo(name)
        if servo is None: continue
        neutral_targets[name] = _clamp_servo_angle(servo, servo.config.neutral_deg)
    return neutral_targets

def _run_demomode_thinking(stop_event: threading.Event) -> None:
    head = _get_anim_servo("NRL")
    left_ear = _get_anim_servo("EAL")
    right_ear = _get_anim_servo("EAR")
    duration = 10.0
    end_time = time.monotonic() + duration
    if head is None and left_ear is None and right_ear is None:
        stop_event.wait(duration)
        return
    head_neutral = head.config.neutral_deg if head else 0.0
    head_left = _clamp_servo_angle(head, head_neutral + 25.0) if head else None
    head_right = _clamp_servo_angle(head, head_neutral - 25.0) if head else None
    left_forward = _clamp_servo_angle(left_ear, left_ear.config.neutral_deg + 12.0) if left_ear else None
    left_back = _clamp_servo_angle(left_ear, left_ear.config.neutral_deg - 12.0) if left_ear else None
    right_forward = _clamp_servo_angle(right_ear, right_ear.config.neutral_deg + 12.0) if right_ear else None
    right_back = _clamp_servo_angle(right_ear, right_ear.config.neutral_deg - 12.0) if right_ear else None

    toggle = False
    while time.monotonic() < end_time and not stop_event.is_set():
        targets: Dict[str, float] = {}
        if head_left is not None and head_right is not None:
            targets["NRL"] = head_left if toggle else head_right
        if left_forward is not None and left_back is not None:
            targets["EAL"] = left_forward if toggle else left_back
        if right_forward is not None and right_back is not None:
            targets["EAR"] = right_back if toggle else right_forward
        _drive_anim_targets(targets, 0.9, stop_event)
        if stop_event.is_set() or time.monotonic() >= end_time: break
        _drive_anim_targets(targets, 0.7, stop_event)
        toggle = not toggle
    neutral_targets = _personality_neutral_targets()
    _drive_anim_targets(neutral_targets, 1.0, stop_event)

def demomode() -> None:
    logger.warning("Attention: Demomode active")
    servo_setup = _initialize_all_servos(logger)
    _initialize_status_led()
    _eyelids_set_mode("auto")
    _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
    try:
        bundle = _setup_face_tracking(logger, servo_setup)
        if bundle:
            tracker, cleanup = bundle
            tracker.start()
    except Exception as e: logger.error(e)
    
    stop_event = threading.Event()
    try:
        while not stop_event.is_set():
            if stop_event.wait(60.0): break
            _led_set_state_safe(CogletState.THINKING)
            _run_demomode_thinking(stop_event)
            _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
    except KeyboardInterrupt: pass
    finally:
        stop_event.set()
        if servo_setup: _cleanup_servo_hardware(servo_setup)

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
    
    # Use provided model or fall back to the default one configured in ENV
    used_model = model if model else os.getenv("OLLAMA_MODEL", "coglet:latest")
    
    # Determine temperature
    if temperature is not None:
        temp_val = temperature
    else:
        temp_val = float(os.getenv("LLM_TEMPERATURE", "0.3"))

    options = {
        "temperature": temp_val,
        "num_ctx": int(os.getenv("LLM_NUM_CTX", "8192")),
    }

    # Pass num_predict if requested (crucial for long emails!)
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
        # Use the flexible timeout here
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "") or ""
    except Exception as e:
        logger.error("[llm] Request failed: %s", e)
        # Propagate exception to allow caller handling
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

# -------------------- Hauptloop --------------------
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

    # --- Hardware Mic (VAD/DOA) ---
    mic_hw = None
    if _XVF_MIC_AVAILABLE:
        mic_hw = ReSpeakerMic(logger)
        mic_hw.start()
    else:
        logger.info("[mic] Hardware VAD/DOA not available")

    # --- Ready prompt ---
    if MODEL_READY:
        logger.info("[pi] model ready → speak")
        say(MODEL_READY)

    # --- Recorder & Wakeword ---
    rec = Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR)
    rec.start()
    kw = Wakeword(WAKEWORD_BACKEND, OWW_MODEL, OWW_THRESHOLD, hw_sr=rec.sr)

    # --- Face Tracking ---
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
    is_deep_sleep = False
    breath_center_pitch = 0.0

    try:
        while not exit_requested and not _shutdown_event.is_set():
            logger.info("[pi] waiting for wakeword")
            
            if not is_deep_sleep:
                _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                _start_idle_animation()
            
            # Wakeword Loop
            wake_detected = False
            while not wake_detected and not _shutdown_event.is_set():
                # 1. Hardware VAD/DOA Check
                if mic_hw:
                    is_speaking, angle = mic_hw.get_status()
                    if is_speaking:
                        # Turn the head towards the speaker here!
                        logger.debug(f"[mic] Speech detected at {angle}°")
                        pass

                # 2. Audio Check
                # NEW: Aggressive processing if buffer grows (catch-up)
                processed_chunks = 0
                max_chunks = 20 # Limit to prevent infinite loop, but high enough to clear ~3s buffer
                
                # We continue processing as long as:
                # - buffer has data
                # - AND (we haven't processed enough chunks OR buffer is still large)
                # This ensures we drain the queue down to a small size.
                while True:
                    if kw.check_once(rec):
                        wake_detected = True
                        break
                    
                    processed_chunks += 1
                    q_size = rec.get_queue_size() if hasattr(rec, "get_queue_size") else rec._q.qsize()
                    
                    # Break conditions:
                    # 1. Empty buffer
                    if q_size == 0:
                        break
                    # 2. Safety limit reached (approx 3.2s of audio)
                    if processed_chunks >= max_chunks:
                        logger.debug("[audio] Wakeword loop lagging? Processed %d chunks, q_size=%d", processed_chunks, q_size)
                        break
                    # 3. Buffer is small enough (real-time), we can sleep a bit
                    if processed_chunks > 1 and q_size < 2:
                        break
 
                if wake_detected: break

                # 3. Deep Sleep Logic
                # Enter Deep Sleep
                now = time.monotonic()
                if not is_deep_sleep and (now - last_activity_ts > DEEP_SLEEP_TIMEOUT_S):
                    logger.info("[pi] Entering Deep Sleep")
                    is_deep_sleep = True
                    say (DS_ACK)
                    if face_tracker: face_tracker.stop()
                    _stop_idle_animation()
                    _eyelids_set_mode("closed")               
                    rec.flush()
                                  
                # Deep Sleep Animation (Loop)
                if is_deep_sleep:
                    phase = math.sin(now * 1.5)
                                  
                    # --- 1. LED Pulse (Color stabilized) ---
                    if _status_led:
                        norm = (phase + 1.0) / 2.0
                        # FIX: Min brightness increased to 0.02 to prevent red shift at low levels
                        brightness = 0.02 + (norm * 0.15)
                        
                        r_val = int(255 * brightness)
                        g_val = int(180 * brightness)
                        
                        # FIX: Force green in very dark range for better yellow impression
                        if r_val > 0 and g_val == 0: 
                            g_val = 1
                        if r_val < 5:
                            g_val = r_val

                        try:
                            if hasattr(_status_led, "_set_rgb"):
                                _status_led._set_rgb(r_val, g_val, 0)
                            elif hasattr(_status_led, "_pixels") and _status_led._pixels:
                                _status_led._pixels.fill((r_val, g_val, 0))
                                _status_led._pixels.show()
                        except Exception: 
                            pass              
                
                time.sleep(0.01)

            if _shutdown_event.is_set(): break

            # --- WAKE UP ---
            if is_deep_sleep:
                logger.info("[pi] Waking up!")
                is_deep_sleep = False
                # Reset Wakeword on wake up to ensure fresh state
                if hasattr(kw, "reset"): kw.reset()
                rec.flush()
                _apply_pose_safe(_personality_neutral_targets())
                _eyelids_set_mode("auto")
                _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                if face_tracker:
                    if hasattr(face_tracker._config, 'patrol_enabled'):
                         object.__setattr__(face_tracker._config, 'patrol_enabled', True)
                    face_tracker.start()
                time.sleep(0.5)

            last_activity_ts = time.monotonic()
            _stop_idle_animation()
            
            logger.info("[pi] wakeword detected")
            # --- Guard: Don't record our own confirm prompt as user input ---
            # half_duplex_tts() does NOT globally mute when BARGE_IN=1
            # (see half_duplex_tts in this file). So we always mute locally here.
            rec.set_listen(False)
            rec.flush()
            say(MODEL_CONFIRM)

            # Let speaker tail decay while we are still muted, then start clean.
            post_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5"))
            if post_cd > 0:
                time.sleep(post_cd)
            rec.flush()
            rec.set_listen(True)
          
            if os.getenv("LLM_RESET_ON_WAKE", "1") in ("1", "true"):
                _conv.reset()

            # Record User
            endpoint = SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr)
            anim_listen_start()
            try:
                # ===> NEU: Hardware-Poll PAUSIEREN für saubere Aufnahme <===
                if mic_hw:
                    mic_hw.set_paused(True)
                pcm, dur = endpoint.record(rec)
            finally:
                # ===> NEU: Hardware-Poll wieder AKTIVIEREN <===
                if mic_hw:
                    mic_hw.set_paused(False)
                anim_listen_stop()

            if len(pcm) < int(0.2 * rec.sr) * 2:
                logger.info("[pi] silence; back to idle")
                continue
            
            last_activity_ts = time.monotonic()
            _led_set_state_safe(CogletState.THINKING)
            
            # STT
            stt = stt_transcribe(pcm, rec.sr)
            user_text = (stt.get("text") or "").strip() if stt else ""
            if not user_text:
                continue
            
            logger.info("[pi] user: %s", user_text)
            if _is_program_exit_command(user_text):
                say(MODEL_BYEBYE)
                break

            # Check for Email Request
            if _is_email_request(user_text):
                _handle_email_request(user_text, rec, kw)
                last_activity_ts = time.monotonic()
                # Skip normal chat & follow-up
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
                speak_and_back_to_idle(reply, rec, kw)
            anim_think_stop()
            last_activity_ts = time.monotonic()

            # --- Follow-Up Window ---
            if os.getenv("FOLLOWUP_ENABLE", "1") in ("1", "true", "True"):
                try: max_turns = int(os.getenv("FOLLOWUP_MAX_TURNS", "10"))
                except: max_turns = 5
                arm_s = float(os.getenv("FOLLOWUP_ARM_S", "3.0"))
                fu_cd = float(os.getenv("FOLLOWUP_COOLDOWN_S", "0.10"))
                turns = 0
                
                while not exit_requested and not _shutdown_event.is_set() and (max_turns == 0 or turns < max_turns):
                    _led_set_state_safe(CogletState.AWAIT_FOLLOWUP)
                    # Guard against capturing our own TTS tail in BARGE_IN mode.
                    # We mute locally during the cooldown so no speaker audio enters the recorder queue.
                    sleep_s = fu_cd
                    if BARGE_IN:
                        try:
                            tts_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5"))
                        except Exception:
                            tts_cd = 0.5
                        sleep_s = max(fu_cd, tts_cd)
                        rec.set_listen(False)

                    time.sleep(sleep_s)
                    rec.flush() # Clean buffer
                    if BARGE_IN:
                        rec.set_listen(True)
                    
                    endpoint = SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr)
                    anim_listen_start()
                    try:
                        # ===> NEU: Hardware-Poll PAUSIEREN für saubere Aufnahme <===
                        if mic_hw:
                            mic_hw.set_paused(True)
                        pcm, dur = endpoint.record(rec, no_speech_timeout_s=arm_s)
                    finally:
                        # ===> NEU: Hardware-Poll wieder AKTIVIEREN <===
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

                    # Check for Email Request (Follow-Up)
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
                        speak_and_back_to_idle(reply, rec, kw)
                        turns += 1
                    anim_think_stop()
                    last_activity_ts = time.monotonic()

            _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
            # After Follow-Up session, ensure we start fresh
            rec.flush()
            if hasattr(kw, "reset"): kw.reset()

    except KeyboardInterrupt:
        logger.info("[pi] interrupted.")
        say(MODEL_BYEBYE)
    finally:
        _shutdown_event.set()
        if mic_hw: mic_hw.stop()
        if face_tracker_cleanup: face_tracker_cleanup()
        _restore_neutral_pose_and_close_lid()
        _cleanup_servo_hardware(servo_setup)
        try: rec.stop()
        except: pass
        try: _led_set_state_safe(CogletState.OFF)
        except: pass

if __name__ == "__main__":
    main()
