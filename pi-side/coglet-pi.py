#!/usr/bin/env python3
"""
Coglet Pi side:
- Wakeword (openwakeword) -> recording (sounddevice + webrtcvad) -> /stt on PC
- /api/chat (Ollama, stream=true) -> sentence buffering -> Piper TTS (FIFO to warm server) -> aplay
- Half-duplex: mic is muted during TTS (blocked for estimated speech duration).

ENV (see /etc/default/coglet-pi):
  STT_URL, OLLAMA_URL, OLLAMA_MODEL, LLM_KEEP_ALIVE
  MIC_SR, MIC_DEVICE
  WAKEWORD_BACKEND, OWW_MODEL, OWW_THRESHOLD
  PIPER_VOICE, PIPER_VOICE_JSON
  PIPER_FIFO (/run/piper/in.jsonl)
  TTS_WPM (e.g., 185), TTS_PUNCT_PAUSE_MS (e.g., 180)
"""

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
import webrtcvad
import errno
import stat
from math import gcd
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Mapping, Optional, Set
from scipy.signal import resample_poly
from openwakeword import Model as _OWWModel
from startup_checks import (
    StartupCheckError,
    check_ollama_model,
    check_piper_mqtt_connectivity,
    check_stt_health,
)

_STATUS_LED_IMPORT_ERROR: Exception | None = None
_status_led_available = (
    importlib.util.find_spec("hardware.status_led") is not None
    and importlib.util.find_spec("neopixel") is not None
    and importlib.util.find_spec("board") is not None
)

if _status_led_available:
    from hardware.status_led import StatusLED, CogletState
else:  # pragma: no cover - optional hardware dependency
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

from hardware.pca9685_servo import Servo, ServoConfig
from hardware.servo_calibration import (
    ServoCalibration,
    apply_calibration_to_config,
    load_servo_calibration,
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
from logging_setup import get_logger, setup_logging

setup_logging()
logger = get_logger()


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
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("ORT_LOG_SEVERITY_LEVEL", "3")

def _parse_device_env(v, default):
    if v is None:
        return default
    v = v.strip()
    if v == "":
        return default
    try:
        # numerischer Index erlaubt
        return int(v)
    except ValueError:
        # auch Namen wie "mic" / "mic_ch0" erlauben
        return v


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
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "wheatley")
LLM_KEEP_ALIVE   = os.getenv("LLM_KEEP_ALIVE", "30m")
MODEL_CONFIRM    = os.getenv("MODEL_CONFIRM", "Ja?")
MODEL_READY      = os.getenv("MODEL_READY", "Alle Subsysteme bereit. Ich erwarte das Wähkwört.")
MODEL_BYEBYE     = os.getenv("MODEL_BYEBYE", "Tschüssen!")
EOC_ACK          = os.getenv("EOC_ACK", "OK. Ich warte aufs neue Wähkwört.")
OWW_MODEL        = os.getenv("OWW_MODEL", "/opt/coglet-pi/.venv/lib/python3.13/site-packages/openwakeword/resources/models/wheatley.onnx")
OWW_THRESHOLD    = float(os.getenv("OWW_THRESHOLD", "0.35"))
OWW_DEBUG        = int(os.getenv("OWW_DEBUG", "0"))
MIC_DEVICE       = _parse_device_env(os.getenv("MIC_DEVICE"), "mic")
MIC_SR           = int(os.getenv("MIC_SR", "16000"))
MIC_GAIN_DB       = float(os.getenv("MIC_GAIN_DB", "0"))
MIC_AUTO_GAIN     = os.getenv("MIC_AUTO_GAIN", "0") == "1"   
MIC_TARGET_DBFS   = float(os.getenv("MIC_TARGET_DBFS", "-18"))
MIC_MAX_GAIN_DB   = float(os.getenv("MIC_MAX_GAIN_DB", "35"))
WAKEWORD_BACKEND = os.getenv("WAKEWORD_BACKEND", "oww") 
VAD_AGGR         = int(os.getenv("VAD_AGGRESSIVENESS", "2"))  
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
    """Initialise all PCA9685 servos, eyelids, and animation bindings."""

    try:
        import board
        import busio
        from adafruit_pca9685 import PCA9685
    except Exception as exc:  # pragma: no cover - optional hardware dependency
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

    def _effective_base_config(idx: int, base: ServoConfig) -> ServoConfig:
        calibration = calibration_map.get(idx)
        if calibration is None:
            return base
        return apply_calibration_to_config(base, calibration)

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
            calibrated_base = _effective_base_config(idx, definition.config)
            config = _create_servo_config(prefix, calibrated_base, pwm_freq, logger=logger)
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

def _register_anim_servos(servos: Mapping[str, Servo]) -> None:
    """Store servo instances for personality/animation servos.

    Tracking servos (eyes, NPT, wheels) are excluded. LID is controlled solely by
    the EyelidController.
    """

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
    """Park servos in their calibrated stop pose and close the eyelid."""

    try:
        _move_servos_to_stop_positions()
    except Exception as exc:
        logger.debug("Servo parking during shutdown failed: %s", exc)

    try:
        _eyelids_set_mode("closed")
    except Exception as exc:
        logger.debug("Closing eyelid failed: %s", exc)


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
    """Update LED status if hardware is available; ignore all errors."""
    if _status_led is None or CogletState is None:
        return
    try:
        _status_led.set_state(state)
    except Exception as exc:
        logger.debug("Status LED update failed: %s", exc)


def _initialize_status_led() -> None:
    """Initialize the optional status LED hardware."""

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
        _clamp_servo_angle(left_ear, left_ear.config.neutral_deg + 12.0)
        if left_ear is not None
        else None
    )
    left_back = (
        _clamp_servo_angle(left_ear, left_ear.config.neutral_deg - 12.0)
        if left_ear is not None
        else None
    )

    right_forward = (
        _clamp_servo_angle(right_ear, right_ear.config.neutral_deg + 12.0)
        if right_ear is not None
        else None
    )
    right_back = (
        _clamp_servo_angle(right_ear, right_ear.config.neutral_deg - 12.0)
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
    _apply_pose_safe(_personality_neutral_targets())


def anim_talk_start():
    logger.info("[anim] talk_start")
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

# -------------------- Global state --------------------
_listen = True         # mic on/off
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
    exits = {
        "programm ende",
        "programmende",
        "programm-ende",
    }
    return normalized in exits or collapsed in exits

def _fifo_write_nonblock(path: str, line: str) -> bool:
    """Try to open FIFO non-blocking and write; return False if no reader."""
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
    """Trigger talk_start once per TTS id to keep start/stop ordering stable."""
    if tts_id in _tts_anim_started:
        return
    _tts_anim_started.add(tts_id)
    anim_talk_start()


def _clear_tts_tracking(tts_id: str) -> None:
    """Clean up bookkeeping for a finished/cancelled/error TTS request."""
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
        _ensure_talk_anim_started(tts_id)
    elif state == "SPEAKING":
        if prev not in ("START", "SPEAKING"):
            if tts_id in _tts_manual_started:
                _tts_manual_started.discard(tts_id)
            _ensure_talk_anim_started(tts_id)
    elif state in ("DONE", "CANCELLED"):
        _ensure_talk_anim_started(tts_id)
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

# -------------------- Utilities --------------------
@contextmanager
def half_duplex_tts():
    global _listen, _tts_active
    _tts_active = True
    try:
        if BARGE_IN:
            # Full-Duplex: Mic bleibt an
            yield
        else:
            _listen = False
            try:
                yield
            finally:
                _listen = True
    finally:
        _tts_active = False

def _voice_sample_rate() -> int:
    """Liest die sample_rate aus der Piper-Voice-JSON; fallback 22050."""
    try:
        with open(PIPER_VOICE_JSON, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return int(cfg.get("audio", {}).get("sample_rate", 22050))
    except Exception:
        return 22050

def estimate_tts_seconds(text: str) -> float:
    """Rough speech duration: WPM plus small extra pauses per punctuation mark."""
    # Count words (simple/robust):
    words = max(1, len(re.findall(r'\b\w+\b', text, flags=re.UNICODE)))
    base = words * (60.0 / max(60, TTS_WPM))
    pauses = text.count('.') + text.count('!') + text.count('?') + text.count('…')
    commas = text.count(',') + text.count(';') + text.count(':')
    extra = pauses * (TTS_PUNCT_MS/1000.0) + commas * (TTS_PUNCT_MS/2000.0)
    return base + extra + 0.2  # small safety margin

def say(text: str):
    """Send text to TTS. Prefer MQTT, fallback to FIFO, else oneshot pipeline."""
    if not text or not text.strip():
        return

    payload = {"text": text}
    line = json.dumps(payload, ensure_ascii=False) + "\n"

    with half_duplex_tts():
        # 1) MQTT preferred
        used = False
        if (TTS_MODE.lower() == "mqtt" or PIPER_MQTT_HOST) and (mqtt is not None):
            est = max(0.6, estimate_tts_seconds(text))
            tts_id = _piper_mqtt_publish(text, estimate_hint=est)
            if tts_id:
                used = True
                # Warten auf Status-Start, ansonsten Fallback-Animation triggern
                deadline = time.time() + 0.6
                while time.time() < deadline:
                    state = _tts_states.get(tts_id)
                    if state in {"START", "SPEAKING", "DONE", "CANCELLED", "ERROR"}:
                        break
                    time.sleep(0.05)
                else:
                    _ensure_talk_anim_started(tts_id)
                    _tts_manual_started.add(tts_id)

                _wait_for_tts_done(
                    tts_id,
                    fallback_seconds=est,
                    hard_timeout=max(6.0, est * 2 + 2.0),
                )
                time.sleep(0.1)
                if _tts_states.get(tts_id) not in {"DONE", "CANCELLED", "ERROR"}:
                    anim_talk_stop()
                _clear_tts_tracking(tts_id)

        if used:
            return

        # 2) FIFO (non-blocking open)
        anim_talk_start()
        if _fifo_write_nonblock(PIPER_FIFO, line):
            if not BARGE_IN:
                time.sleep(estimate_tts_seconds(text))
            anim_talk_stop()
            return
        anim_talk_stop()

        # 3) One-shot fallback (slow; loads model each time)
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
            try:
                anim_talk_stop()
            except Exception:
                pass

def chat_stream(prompt: str):
    """Ollama Streaming-Generator: liefert Text-Inkremente"""
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
            if not line:
                continue
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            if j.get("done"):
                break
            msg = j.get("message", {})
            chunk = msg.get("content", "")
            if chunk:
                yield chunk

# --- Speech endpointing (WebRTC-VAD, robust start + end-guard) ---
class SpeechEndpoint:
    def __init__(self, sr: int, vad_aggr: int = 2):
        self.sr = int(sr)
        self.vad = webrtcvad.Vad(int(vad_aggr))

        # Tuning (override via ENV)
        self.frame_ms       = int(os.getenv("VAD_FRAME_MS", "30"))          # 10/20/30
        self.start_win      = int(os.getenv("VAD_START_WIN", "5"))          # majority window
        self.start_min      = int(os.getenv("VAD_START_MIN", "3"))          # minimum speech votes in window
        self.start_consec   = int(os.getenv("VAD_START_CONSEC_MIN", "3"))   # consecutive speech frames
        self.end_hang_ms    = int(os.getenv("VAD_END_HANG_MS", "400"))      # hangover after silence
        self.end_guard_ms   = int(os.getenv("VAD_END_GUARD_MS", "1200"))    # minimum duration from start to silence end
        self.preroll_ms     = int(os.getenv("VAD_PREROLL_MS", "240"))       # buffer pre-roll
        self.max_utter      = float(os.getenv("MAX_UTTER_S", "8.0"))        # hard upper bound
        self.no_speech      = float(os.getenv("NO_SPEECH_TIMEOUT_S", "3.0"))# timeout before start

        # Sanity
        if self.frame_ms not in (10, 20, 30):
            self.frame_ms = 30
        if self.sr not in (8000, 16000, 32000, 48000):
            raise ValueError("Sample rate must be 8000/16000/32000/48000 for WebRTC-VAD")

        # Derived values
        self.frame_samples  = (self.sr * self.frame_ms) // 1000
        self.frame_bytes    = self.frame_samples * 2  # int16 mono
        self.hang_frames    = max(1, math.ceil(self.end_hang_ms / self.frame_ms))
        self.preroll_frames = max(0, self.preroll_ms // self.frame_ms)
        self.end_guard_s    = self.end_guard_ms / 1000.0

    def record(self, recorder, no_speech_timeout_s: float | None = None, end_guard_s: float | None = None):
        local_no_speech = float(no_speech_timeout_s) if no_speech_timeout_s is not None else self.no_speech
        local_end_guard = float(end_guard_s) if end_guard_s is not None else self.end_guard_s

        votes = collections.deque(maxlen=self.start_win)
        preroll = collections.deque(maxlen=self.preroll_frames)
        buf = io.BytesIO()

        start_ts = time.monotonic()
        speech_started = False
        started_at = None
        frames_since_end = 0
        consec_speech = 0

        while True:
            now = time.monotonic()
            # Safety: vor Start -> local_no_speech, nach Start -> max_utter
            if (now - start_ts) > (self.max_utter if speech_started else local_no_speech):
                break

            frame = recorder.read_bytes(self.frame_bytes)
            if not frame:
                continue

            is_speech = self.vad.is_speech(frame, self.sr)

            if not speech_started:
                votes.append(1 if is_speech else 0)
                if self.preroll_frames:
                    preroll.append(frame)
                consec_speech = (consec_speech + 1) if is_speech else 0

                if len(votes) == self.start_win and sum(votes) >= self.start_min and consec_speech >= self.start_consec:
                    for fr in preroll:
                        buf.write(fr)
                    buf.write(frame)
                    speech_started = True
                    started_at = now
                    frames_since_end = 0
                else:
                    continue
            else:
                buf.write(frame)
                if is_speech:
                    frames_since_end = 0
                else:
                    frames_since_end += 1
                    if frames_since_end >= self.hang_frames and (now - started_at) >= local_end_guard:
                        break

        pcm = buf.getvalue()
        dur_s = len(pcm) / (2 * self.sr)
        return pcm, dur_s

# ===== Recorder: ein InputStream, int16-Pipeline mit optionalem AGC =====
class Recorder:
    """
    Audio-Recorder auf Basis sounddevice.RawInputStream.
    - read(n_samples) -> np.float32 mono [-1..1] (mit Software-Gain)
    - read_bytes(n_bytes) -> raw PCM16 bytes
    - flush()/clear_queue()/flush_input_buffers(): clear buffers
    - Half-duplex: via self._listen (and backward compatible through global _listen)
    """
    def __init__(self, sr=16000, vad_aggr=2):
        self.sr = int(sr)
        self.vad_aggr = int(vad_aggr)

        # MIC environment
        dev_env = os.getenv("MIC_DEVICE", "0")
        self.device = dev_env if not dev_env.isdigit() else int(dev_env)
        self.gain_db = float(os.getenv("MIC_GAIN_DB", "0"))
        self.auto_gain = os.getenv("MIC_AUTO_GAIN", "0") in ("1", "true", "True")
        self.target_dbfs = float(os.getenv("MIC_TARGET_DBFS", "-18"))
        self.max_gain_db = float(os.getenv("MIC_MAX_GAIN_DB", "35"))

        # Software gain (only for read -> float32)
        self._lin_gain = float(10.0 ** (self.gain_db / 20.0)) if self.gain_db else 1.0

        # Buffers
        self._q = queue.Queue()          # collects raw bytes (int16 mono)
        self._resid = b""                # any remaining bytes between reads
        self._level_buf = np.empty(0, dtype=np.float32)  # for level display
        self._level_max_sec = 2.0

        # Stream/Thread
        self._stream = None
        self._running = False

        # Half-duplex flag (instance-wide); stays backward compatible with global _listen
        self._listen = True

        logger.info(
            "[pi] MIC env: device=%s sr=%s gain_db=%.1f agc=%s target=%.1f max=%.1f",
            self.device,
            self.sr,
            self.gain_db,
            self.auto_gain,
            self.target_dbfs,
            self.max_gain_db,
        )

        # ---- internal callback for the stream ----
    def _callback(self, indata, frames, time_info, status):
        # indata: bytes (RawInputStream, dtype='int16', channels=1)
        if status:
            logger.warning("[rec] status: %s", status)

        # During TTS do NOT enqueue:
        # - Primary: instance-wide flag self._listen
        # - Additionally: global _listen for backward compatibility with half_duplex_tts()
        if not (getattr(self, "_listen", True) and globals().get("_listen", True)):
            return  # important: no queue put, no level update

        # Raw data into queue
        self._q.put(bytes(indata))

        # For level display (float32 mono)
        x = np.frombuffer(indata, dtype="<i2").astype(np.float32) / 32768.0
        # Trim level buffer if needed
        max_len = int(self._level_max_sec * self.sr)
        if self._level_buf.size == 0:
            self._level_buf = x
        else:
            need_cut = max(0, (self._level_buf.size + x.size) - max_len)
            if need_cut:
                self._level_buf = self._level_buf[need_cut:]
            self._level_buf = np.concatenate((self._level_buf, x), dtype=np.float32)

    # ---- Start/Stop ----
    def start(self):
        if self._running:
            return
        self._stream = sd.RawInputStream(
            samplerate=self.sr,
            channels=1,
            dtype="int16",
            blocksize=0,         # PortAudio picks automatically
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()
        self._running = True

        # kurzen Moment sammeln, dann Pegel zeigen
        time.sleep(0.25)
        db = self.mic_level_dbfs()
        if db is not None:
            logger.info(
                "[pi] mic level ≈ %.1f dBFS (post-gain, target %.0f dBFS, gain %.1f dB)",
                db,
                self.target_dbfs,
                self.max_gain_db,
            )

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._running = False
        self.flush()

    # ---- Utility ----
    def mic_level_dbfs(self):
        if self._level_buf.size == 0:
            return None
        # RMS in dBFS, aber "post-gain" (was OWW sieht)
        x = self._level_buf * self._lin_gain
        rms = float(np.sqrt(np.mean(np.square(x)) + 1e-12))
        db = 20.0 * np.log10(rms + 1e-12)
        return db

    def flush(self):
        """Alles verwerfen: Queue + Rest + Level-Buffer."""
        dropped = 0
        if hasattr(self, "_q") and self._q is not None:
            try:
                while True:
                    self._q.get_nowait()
                    dropped += 1
            except queue.Empty:
                pass
        self._resid = b""
        self._level_buf = np.empty(0, dtype=np.float32)
        if dropped:
            logger.debug("[rec] flush: dropped %d chunks", dropped)

    # Aliases for different callers
    def clear_queue(self):
        self.flush()

    def flush_input_buffers(self):
        self.flush()

    # ---- Read as float32 (for OWW) ----
    def read(self, n_samples):
        """
        Block until n_samples float32 mono samples are available (with software gain).
        Uses the raw bytes from the queue. """
        need = int(n_samples) * 2  # 2 bytes/sample (int16)
        data = bytearray()

        # include any remainder first
        if self._resid:
            data.extend(self._resid)
            self._resid = b""

        # pull from queue until enough
        while len(data) < need:
            chunk = self._q.get()  # blockierend
            data.extend(chunk)

        # park any overflow
        if len(data) > need:
            self._resid = bytes(data[need:])
            data = data[:need]

        # nach float32 wandeln + Gain
        x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if self._lin_gain != 1.0:
            x = np.clip(x * self._lin_gain, -1.0, 1.0, out=x)
        return x

    # ---- Read as bytes (for STT/storage) ----
    def read_bytes(self, n_bytes):
        """
        Block until n_bytes raw bytes (int16 mono) are available.
        No software gain applied here!
        """
        need = int(n_bytes)
        data = bytearray()

        if self._resid:
            take = min(len(self._resid), need)
            data.extend(self._resid[:take])
            self._resid = self._resid[take:]

        while len(data) < need:
            chunk = self._q.get()
            data.extend(chunk)

        if len(data) > need:
            self._resid = bytes(data[need:])
            data = data[:need]

        return bytes(data)

# --- Wakeword (openWakeWord, int16 @ 16 kHz, 80-ms-aligned, refractory/re-arm) ---
class Wakeword:
    """
    expects Recorder.read(n_hw) -> float32 @ hw_sr (mono), delivers blocking until WAKE.
    Core features:
    - OWW wants 16 kHz INT16; we resample 48k/32k to 16k float32 and cast to int16 just before calling predict().
    - Windows/Hops are multiples of 80 ms (1280 samples @16k) => stable scores.
    - Refractory period + Re-Arm: avoids re-triggering immediately after WAKE or after TTS.
    - reset_after_tts(): nullify ring-buffer + suppress shortly after TTS.
    """

    def __init__(self, backend: str, model_path: str, threshold: float,
                 hw_sr: int = None, sr: int = None):
        # ---- New: alias support for sr= ----
        if hw_sr is None and sr is not None:
            hw_sr = sr
        if hw_sr is None:
            raise TypeError("Wakeword.__init__() requires a hardware sample rate: pass hw_sr= or sr=")

        b = (backend or "oww").lower()
        if b not in ("oww", "openwakeword"):
            raise ValueError("WAKEWORD_BACKEND must be 'oww' or 'openwakeword'")

        self.hw_sr     = int(hw_sr)   # 48000
        self.oww_sr    = 16000        # OWW expects 16 kHz
        self.threshold = float(threshold)
        self.debug     = os.getenv("OWW_DEBUG", "0") != "0"

        g = gcd(self.hw_sr, self.oww_sr)
        self._up   = self.oww_sr // g
        self._down = self.hw_sr  // g

        M = 1280
        win_ms = int(os.getenv("OWW_WIN_MS", "800"))
        hop_ms = int(os.getenv("OWW_HOP_MS", "160"))
        self.win_oww = max(M, (self.oww_sr * win_ms // 1000) // M * M)
        self.hop_oww = max(M, (self.oww_sr * hop_ms // 1000) // M * M)
        self.hop_hw  = int(self.hop_oww * self._down / self._up)

        self.rearm_ratio    = float(os.getenv("WAKE_REARM_RATIO", "0.6"))
        self.min_gap_s      = float(os.getenv("WAKE_MIN_GAP_S", "1.5"))
        self.rearm_low_n    = int(os.getenv("WAKE_REARM_LOW_COUNT", "3"))
        self.after_tts_s    = float(os.getenv("OWW_SUPPRESS_AFTER_TTS_S", "0.8"))
        self.last_wake_ts   = 0.0
        self.suppress_until = 0.0
        self.armed          = True
        self._below_consec  = 0
        self._was_above     = False

        self.detector = _OWWModel(
            wakeword_models=[model_path],
            inference_framework="onnx",
        )
        probe = np.zeros(self.win_oww, dtype=np.int16)
        keys  = self.detector.predict(probe)
        self.key = next(iter(keys.keys()))

        if self.debug:
            logger.debug(
                "[oww] using model: %s  threshold=%s hw_sr=%s  oww_sr=%s",
                model_path,
                self.threshold,
                self.hw_sr,
                self.oww_sr,
            )
            logger.debug(
                "[oww] resampler: scipy.resample_poly (up/down=%s/%s) win=%s hop=%s (80-ms multiples)",
                self._up,
                self._down,
                self.win_oww,
                self.hop_oww,
            )

        self.ring = np.zeros(self.win_oww, dtype=np.float32)
    
    # ---------- Public hooks ----------
    def reset_after_tts(self):
        """After TTS: clear the ring, set a short suppression window, rebuild re-arm."""
        self.ring.fill(0.0)
        self._below_consec  = 0
        self._was_above     = False
        self.armed          = False
        self.suppress_until = time.monotonic() + self.after_tts_s

    def wait(self, recorder) -> None:
        """
        Block until the wakeword is detected.
        recorder.read(n_hw) soll float32 @ self.hw_sr liefern (mono).
        """
        # Prime the ring buffer first (4 hops)
        need_hw = self.hop_hw * 4
        acc = np.empty(0, dtype=np.float32)
        while acc.size < need_hw:
            chunk = recorder.read(self.hop_hw)
            if chunk is None or chunk.size == 0:
                time.sleep(0.005); continue
            if chunk.ndim > 1:
                chunk = chunk.reshape(-1)
            acc = np.concatenate([acc, chunk.astype(np.float32, copy=False)])

        y0 = self._resample_hw_to_oww(acc[:need_hw])
        if y0.size:
            if y0.size >= self.win_oww:
                self.ring[:] = y0[-self.win_oww:]
            else:
                self.ring[-y0.size:] = y0

        # Hauptloop
        while True:
            chunk = recorder.read(self.hop_hw)
            if chunk is None or chunk.size == 0:
                time.sleep(0.005); continue
            if chunk.ndim > 1:
                chunk = chunk.reshape(-1)
            y = self._resample_hw_to_oww(chunk.astype(np.float32, copy=False))

            if y.size >= self.win_oww:
                self.ring[:] = y[-self.win_oww:]
            elif y.size > 0:
                self.ring = np.roll(self.ring, -y.size)
                self.ring[-y.size:] = y

            score = self._predict(self.ring)
            if self.debug:
                logger.debug("[oww] score=%.3f (%s)", score, self.key)

            now = time.monotonic()
            rearm_level = self.threshold * self.rearm_ratio

            # Build re-arm criterion
            if score < rearm_level:
                self._below_consec += 1
            else:
                self._below_consec = 0

            # Suppression window after TTS / last WAKE
            if now < self.suppress_until:
                self._was_above = (score >= self.threshold)
                continue

            # Not armed again yet? Wait for sufficiently low scores in a row
            if not self.armed:
                if self._below_consec >= self.rearm_low_n:
                    self.armed = True
                self._was_above = (score >= self.threshold)
                if not self.armed:
                    continue

            # Rising-Edge + armed → WAKE
            if score >= self.threshold and not self._was_above and self.armed:
                logger.info("[oww] WAKE")
                self.last_wake_ts   = now
                self.armed          = False
                self.suppress_until = now + self.min_gap_s  # refractory window
                self._was_above     = True
                return

            # keep track of Edge-Status 
            self._was_above = (score >= self.threshold)

    # ---------- internal ----------
    def _resample_hw_to_oww(self, x_hw_f32: np.ndarray) -> np.ndarray:
        if x_hw_f32 is None or x_hw_f32.size == 0:
            return np.empty(0, dtype=np.float32)
        return resample_poly(
            x_hw_f32.astype(np.float32, copy=False),
            self._up, self._down
        ).astype(np.float32, copy=False)

    def _predict(self, y_oww_f32: np.ndarray) -> float:
        # crop down to multiple of 80-ms (1280 Samples @16k)
        M = 1280
        n = (y_oww_f32.size // M) * M
        if n == 0:
            return 0.0
        y = y_oww_f32[:n]
        # float32 [-1..1] → int16 PCM
        y16 = (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16, copy=False)
        out = self.detector.predict(y16)
        return float(out[self.key])

# --- Flush-Helper (uses Recorder.flush_input_buffers / flush / known queue-names) ---
def _flush_input_buffers(recorder):
    # native methode available?
    if hasattr(recorder, "flush_input_buffers") and callable(recorder.flush_input_buffers):
        try:
            recorder.flush_input_buffers()
            return
        except Exception:
            pass
    # Fallback to flush()
    if hasattr(recorder, "flush") and callable(recorder.flush):
        try:
            recorder.flush()
            return
        except Exception:
            pass
    # flush internaö queues
    for name in ("_q", "q", "_queue", "queue", "input_queue", "_input_q"):
        q = getattr(recorder, name, None)
        if q is None:
            continue
        try:
            while True:
                q.get_nowait()
        except Exception:
            pass

# --- TTS + back to idle (uses say() exclusively) ---
def speak_and_back_to_idle(text: str, recorder, kw):
    """
    Speak text via the FIFO-based say(), avoid echo (mute mic),
    drain input buffers, and cleanly re-arm the wakeword.
    """

    if not BARGE_IN:
        # Hard-close mic: callback MUST NOT enqueue (see Recorder._callback AND logic)
        setattr(recorder, "_listen", False)
        # Clear input buffers before TTS
        _flush_input_buffers(recorder)

    # TTS (the warm-Start-Pipeline with half_duplex_tts + estimate_tts_seconds)
    say(text)

    if not BARGE_IN:
        post_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5"))
        if post_cd > 0:
            time.sleep(post_cd)
        _flush_input_buffers(recorder)
        kw.reset_after_tts()
        setattr(recorder, "_listen", True)
    else:
        # Mic stays open; wakeword/ASR may continue
        kw.reset_after_tts()

def chat_once(user_text: str) -> str:
    """Non-streaming LLM call: accumulate all chunks into one answer."""
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

# ===== Conversation buffer =====
class ConversationMemory:
    def __init__(self, max_turns: int = 6, system_prompt: str | None = None):
        self.max_turns = int(max_turns)
        self.system_prompt = (system_prompt or "").strip() or None
        self._pairs = collections.deque()  # list of (user, assistant)

    def reset(self):
        self._pairs.clear()

    def add_user(self, text: str):
        # Placeholder: we record a pair after each reply
        self._last_user = text

    def add_assistant(self, text: str):
        u = getattr(self, "_last_user", None)
        if u is None:
            u = ""
        self._pairs.append((u, text))
        # trim
        while self.max_turns > 0 and len(self._pairs) > self.max_turns:
            self._pairs.popleft()
        # cleanup
        self._last_user = None

    def build_messages(self, current_user: str):
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        for u, a in list(self._pairs):
            if u:
                msgs.append({"role": "user", "content": u})
            if a:
                msgs.append({"role": "assistant", "content": a})
        msgs.append({"role": "user", "content": current_user})
        return msgs

_conv = ConversationMemory(
    max_turns=int(os.getenv("LLM_CTX_TURNS", "6")),
    system_prompt=os.getenv("LLM_SYSTEM_PROMPT", "")
)


def _run_startup_checks(logger: logging.Logger) -> None:
    """Validate that STT, Ollama, and Piper are reachable before starting."""

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


def _drive_anim_targets(
    targets: Mapping[str, float], duration_s: float, stop_event: threading.Event
) -> None:
    """Move animation servos towards the desired targets for a duration."""

    servo_targets: Dict[Servo, float] = {}
    for name, angle in targets.items():
        servo = _get_anim_servo(name)
        if servo is None:
            continue
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
        if stop_event.wait(dt):
            break
        for servo in servo_targets:
            servo.update(dt)
        remaining -= dt


def _personality_neutral_targets() -> Dict[str, float]:
    """Return neutral angles for all personality servos that are available."""

    neutral_targets: Dict[str, float] = {}
    for name in PERSONALITY_SERVO_NAMES:
        servo = _get_anim_servo(name)
        if servo is None:
            continue
        neutral_targets[name] = _clamp_servo_angle(servo, servo.config.neutral_deg)
    return neutral_targets


def _run_demomode_thinking(stop_event: threading.Event) -> None:
    """Play the periodic thinking animation with alternating ears."""

    head = _get_anim_servo("NRL")
    left_ear = _get_anim_servo("EAL")
    right_ear = _get_anim_servo("EAR")

    duration = 10.0
    end_time = time.monotonic() + duration

    if head is None and left_ear is None and right_ear is None:
        logger.debug("Demomode thinking animation skipped (no servos available)")
        stop_event.wait(duration)
        return

    head_neutral = head.config.neutral_deg if head is not None else 0.0
    head_left = _clamp_servo_angle(head, head_neutral + 25.0) if head is not None else None
    head_right = _clamp_servo_angle(head, head_neutral - 25.0) if head is not None else None

    left_forward = (
        _clamp_servo_angle(left_ear, left_ear.config.neutral_deg + 12.0)
        if left_ear is not None
        else None
    )
    left_back = (
        _clamp_servo_angle(left_ear, left_ear.config.neutral_deg - 12.0)
        if left_ear is not None
        else None
    )

    right_forward = (
        _clamp_servo_angle(right_ear, right_ear.config.neutral_deg + 12.0)
        if right_ear is not None
        else None
    )
    right_back = (
        _clamp_servo_angle(right_ear, right_ear.config.neutral_deg - 12.0)
        if right_ear is not None
        else None
    )

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
        if stop_event.is_set() or time.monotonic() >= end_time:
            break
        _drive_anim_targets(targets, 0.7, stop_event)
        toggle = not toggle

    neutral_targets = _personality_neutral_targets()
    _drive_anim_targets(neutral_targets, 1.0, stop_event)


def demomode() -> None:
    """Run Coglet in demo mode without audio, network, or MQTT traffic."""

    logger.warning("Attention: Demomode active")
    servo_setup = _initialize_all_servos(logger)
    _initialize_status_led()
    _eyelids_set_mode("auto")
    _apply_pose_safe("pose_rest")
    _led_set_state_safe(CogletState.AWAIT_WAKEWORD)

    face_tracker = None
    face_tracker_cleanup: Callable[[], None] | None = None
    try:
        bundle = _setup_face_tracking(logger, servo_setup)
    except Exception as exc:
        logger.error("Face tracking setup failed: %s", exc)
        bundle = None
    if bundle:
        face_tracker, face_tracker_cleanup = bundle
        try:
            face_tracker.start()
            logger.info("Face tracking thread running (enabled=%s)", FACE_TRACKING_ENABLED)
        except Exception as exc:
            logger.error("Face tracking start failed: %s", exc)
            if face_tracker_cleanup is not None:
                face_tracker_cleanup()
            face_tracker = None
            face_tracker_cleanup = None
    else:
        logger.info("Face tracking not initialised; see previous log lines for details")

    stop_event = threading.Event()
    try:
        while not stop_event.is_set():
            if stop_event.wait(60.0):
                break
            _led_set_state_safe(CogletState.THINKING)
            _run_demomode_thinking(stop_event)
            _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
    except KeyboardInterrupt:
        logger.info("Demomode interrupted by user.")
    finally:
        stop_event.set()
        if face_tracker_cleanup is not None:
            try:
                face_tracker_cleanup()
            except Exception as exc:
                logger.debug("Face tracking cleanup failed: %s", exc)
        try:
            _restore_neutral_pose_and_close_lid()
        except Exception as exc:
            logger.debug("Neutral pose restore during demomode shutdown failed: %s", exc)
        _cleanup_servo_hardware(servo_setup)
        try:
            _led_set_state_safe(CogletState.OFF)
        except Exception:
            pass

def _ollama_chat(messages: list[dict]) -> str:
    """Direkter Chat-Aufruf gegen Ollama /api/chat mit Verlauf."""
    url = f"{os.getenv('OLLAMA_URL','http://192.168.10.161:11434').rstrip('/')}/api/chat"
    model = os.getenv("OLLAMA_MODEL", "wheatley")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.3")),
            "num_ctx": int(os.getenv("LLM_NUM_CTX", "8192")),
        },
        "keep_alive": LLM_KEEP_ALIVE,
    }
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    js = r.json()
    # Ollama /api/chat: Antwort steckt in js["message"]["content"]
    return (js.get("message", {}) or {}).get("content", "") or ""

# -------------------- Hauptloop --------------------
def main():
    logger.info("[pi] Coglet PI starting. STT: %s OLLAMA: %s MODEL: %s", STT_URL, OLLAMA_URL, OLLAMA_MODEL)
    logger.info("[rec] Using MIC_DEVICE=%r, MIC_SR=%s", MIC_DEVICE, MIC_SR)

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

    # --- Status-LED ---
    _initialize_status_led()

    # --- Ready prompt ---
    if MODEL_READY:
        logger.info("[pi] model ready → speak")
        say(MODEL_READY)
        logger.info("[piper] say %s", MODEL_READY)

    # --- Recorder starten ---
    rec = Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR)
    rec.start()

    # --- Wakeword vorbereiten ---
    kw = Wakeword(WAKEWORD_BACKEND, OWW_MODEL, OWW_THRESHOLD, sr=rec.sr)

    face_tracker = None
    face_tracker_cleanup: Callable[[], None] | None = None
    try:
        bundle = _setup_face_tracking(logger, servo_setup)
    except Exception as exc:
        logger.error("Face tracking setup failed: %s", exc)
        bundle = None
    if bundle:
        face_tracker, face_tracker_cleanup = bundle
        try:
            face_tracker.start()
            logger.info("Face tracking thread running (enabled=%s)", FACE_TRACKING_ENABLED)
        except Exception as exc:
            logger.error("Face tracking start failed: %s", exc)
            if face_tracker_cleanup is not None:
                face_tracker_cleanup()
            face_tracker = None
            face_tracker_cleanup = None
    else:
        logger.info("Face tracking not initialised; see previous log lines for details")

    # ===== STT: PCM -> WAV -> HTTP =====
    # ---------- Helper: PCM16 → WAV-Bytes (optional Resample) ----------
    def _pcm16_to_wav_bytes(pcm_bytes: bytes, sr_in: int, sr_out: int | None = None) -> bytes:
        """int16-PCM (mono, LE) in WAV verpacken, optional auf sr_out resamplen."""
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
            w.setsampwidth(2)  # 16-bit
            w.setframerate(used_sr)
            w.writeframes(data_i16.tobytes())
        return buf.getvalue()

    # ---------- Helper: STT via HTTP (WAV) ----------
    def _http_stt_request(pcm_bytes: bytes, sample_rate: int) -> dict | None:
        """
        Primärer STT-Pfad: WAV an Faster-Whisper-HTTP-Server schicken.
        Nutzt STT_URL, STT_RESAMPLE_TO_HZ (Default 16000), STT_LANG (Default 'de').
        """
        try:
            target_sr = int(os.getenv("STT_RESAMPLE_TO_HZ", "16000"))
            lang      = os.getenv("STT_LANG", "de")
            wav_bytes = _pcm16_to_wav_bytes(pcm_bytes, sample_rate, target_sr)
            url = f"{STT_URL.rstrip('/')}/stt"
            files = {"audio": ("utt.wav", wav_bytes, "audio/wav")}
            # Dein Server erwartet 'lang' (nicht 'language'):
            data  = {"lang": lang}
            logger.info(f"[stt] http → POST {url} (wav {len(wav_bytes)} bytes, sr={target_sr}, lang={lang})")
            r = requests.post(url, files=files, data=data, timeout=60)
            if r.status_code >= 400:
                logger.info(f"[stt] http -> {r.status_code}: {r.text}")
            r.raise_for_status()
            js = r.json()
            logger.info(f"[stt] http ✓ text={js.get('text','')!r}")
            return js
        except requests.exceptions.ConnectionError as e:
            logger.info("[stt] server unreachable: %s", e)
            return None
        except Exception as e:
            logger.exception("[stt] http ✗ %s", e)
            return None

    def stt_transcribe(pcm_bytes: bytes, sample_rate: int) -> dict | None:
        """Schlanker Primary-Pfad: immer HTTP-WAV."""
        return _http_stt_request(pcm_bytes, sample_rate)

    # ---------- LLM-Fallback: Ollama /api/generate ----------
    def _fallback_chat_once(prompt: str) -> str:
        try:
            url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
            payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
            logger.info(f"[llm] fallback → POST {url} ({len(prompt)} chars)")
            r = requests.post(url, json=payload, timeout=120)
            r.raise_for_status()
            js = r.json()
            resp = (js.get("response") or "").strip()
            logger.info(f"[llm] fallback ✓ {len(resp)} chars")
            return resp
        except Exception as e:
            logger.exception(f"[llm] fallback ✗ {e}")
            return ""

    # ---------- LLM-Wrapper (nutzt chat_once(), sonst Fallback) ----------
    def llm_chat_once(user_text: str) -> str:
        """
        Konversationsfähiger LLM-Aufruf:
        - Wenn LLM_USE_CHAT=1: nutze _ollama_chat mit Verlauf (_conv)
        - Sonst: Projektroutine chat_once oder Fallback (wie bisher)
        """
        use_chat = os.getenv("LLM_USE_CHAT", "1") in ("1","true","True")

        if use_chat:
            try:
                msgs = _conv.build_messages(user_text)
                resp = _ollama_chat(msgs)
                return resp.strip()
            except Exception as e:
                # Fallback auf Projektfunktion, falls vorhanden
                logger.warning("[llm] chat-path error: %s; falling back …", e)
                pass

        # Projektspezifisch vorhanden?
        if "chat_once" in globals() and callable(globals()["chat_once"]):
            try:
                return (globals()["chat_once"](user_text) or "").strip()
            except Exception as e:
                logger.warning("[llm] project ✗ %s; fallback HTTP …", e)

        # Letzter Fallback: /api/generate (ohne Verlauf)
        return _fallback_chat_once(user_text).strip()

    # --- Hauptloop ---
    exit_requested = False

    try:
        while not exit_requested and not _shutdown_event.is_set():
            logger.info("[pi] waiting for wakeword")
            _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
            kw.wait(rec)  # blocks until WAKE (with refractory/re-arm)
            logger.info("[pi] wakeword detected → speak")
            # say("Ja?")
            say(MODEL_CONFIRM)
            logger.info("[piper] say %s", MODEL_CONFIRM)
            
            # NEW: Verlauf beim neuen Wake leeren (konfigurierbar)
            if os.getenv("LLM_RESET_ON_WAKE", "1") in ("1", "true", "True"):
                _conv.reset()

            # Aufnahme mit sauberem Endpointing (WebRTC-VAD)
            endpoint = SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr)
            anim_listen_start()
            try:
                pcm_bytes, dur_s = endpoint.record(rec)
            finally:
                anim_listen_stop()
            logger.info("[pi] recorded ~%.2fs, %d bytes @%sHz/16-bit/mono", dur_s, len(pcm_bytes), rec.sr)

            # Nichts gesagt?
            if len(pcm_bytes) < int(0.2 * rec.sr) * 2:
                logger.info("[pi] no speech detected; back to idle")
                _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                continue

            # STT
            logger.info("[stt] send → server")
            _led_set_state_safe(CogletState.THINKING)
            stt = stt_transcribe(pcm_bytes, rec.sr)
            if not stt or not (stt.get("text") or "").strip():
                logger.info("[stt] empty result; back to idle")
                _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                continue

            user_text = stt["text"].strip()
            logger.info("[pi] user: %s", user_text)

            if _is_program_exit_command(user_text):
                logger.info("[pi] program exit command received → shutdown")
                say(MODEL_BYEBYE)
                exit_requested = True
                _shutdown_event.set()
                break

            _conv.add_user(user_text)
            anim_think_start()
            try:
                reply = llm_chat_once(user_text) or ""
            finally:
                anim_think_stop()
            _conv.add_assistant(reply)

            t0 = time.monotonic()
            logger.info("[pi] assistant: %s", reply)
            logger.info("[tts] start (%d chars)", len(reply))
            speak_and_back_to_idle(reply, rec, kw)
            t1 = time.monotonic()
            logger.info("[tts] done dt=%.3fs", t1 - t0)

            # --- Follow-up Conversational Window ---
            if os.getenv("FOLLOWUP_ENABLE", "1") in ("1", "true", "True"):
                try:
                    max_turns = int(os.getenv("FOLLOWUP_MAX_TURNS", "10"))
                except Exception:
                    max_turns = 5
                arm_s = float(os.getenv("FOLLOWUP_ARM_S", "3.0"))
                fu_cd = float(os.getenv("FOLLOWUP_COOLDOWN_S", "0.10"))

                turns = 0
                _led_set_state_safe(CogletState.AWAIT_FOLLOWUP)
                while not exit_requested and not _shutdown_event.is_set() and (max_turns == 0 or turns < max_turns):
                    turns += 1
                    _led_set_state_safe(CogletState.AWAIT_FOLLOWUP)
                    # sehr kurzer Cooldown + Ring-Puffer leeren (Echo vermeiden)
                    time.sleep(fu_cd)
                    rec.flush()
                    logger.info("[pi] follow-up window (<= %.1fs) …", arm_s)

                    # Aufnahme nur, wenn innerhalb des Fensters Sprache startet:
                    endpoint = SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr)
                    anim_listen_start()
                    try:
                        pcm_bytes, dur_s = endpoint.record(rec, no_speech_timeout_s=arm_s)  # end guard unchanged
                    finally:
                        anim_listen_stop()

                    if len(pcm_bytes) < int(0.2 * rec.sr) * 2:
                        logger.info("[pi] no follow-up detected; back to wakeword")
                        text = EOC_ACK or MODEL_BYEBYE  
                        say(text)
                        logger.info("[piper] say %s", text)
                        _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                        break  # return to wakeword

                    logger.info("[pi] follow-up recorded ~%.2fs, %d bytes", dur_s, len(pcm_bytes))

                    # STT
                    logger.info("[stt] send → server (follow-up)")
                    _led_set_state_safe(CogletState.THINKING)
                    stt = stt_transcribe(pcm_bytes, rec.sr)
                    if not stt or not (stt.get("text") or "").strip():
                        logger.info("[stt] empty follow-up; back to wakeword")
                        say(MODEL_BYEBYE)
                        logger.info("[piper] say %s", MODEL_BYEBYE)
                        _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                        break

                    user_text = stt["text"].strip()
                    logger.info("[pi] user (follow-up): %s", user_text)

                    if _is_program_exit_command(user_text):
                        logger.info("[pi] follow-up: program exit command → shutdown")
                        say(MODEL_BYEBYE)
                        exit_requested = True
                        _shutdown_event.set()
                        break

                    # optional: „Exit“-Worte beenden die Konversation sofort
                    def _norm_endphrase(s: str) -> str:
                        # kleinschreibung, Satzzeichen raus, Mehrfach-Spaces komprimieren
                        s = (s or "").lower().strip()
                        s = re.sub(r"[^\wäöüß\s-]+", " ", s)   # Umlaute erhalten
                        s = re.sub(r"\s+", " ", s)
                        return s

                    END_EXIT = {
                        # DE
                        "danke", "nein", "nein danke", "nichts", "nichts danke", "nichts danke schön",
                        "nichts danke sehr", "nichts, danke", "passt", "alles gut", "das war's", "das wars",
                        "stop", "stopp", "tschüss", "tschüssen", "abbrechen", "genug", "fertig",
                        # EN (falls mal englische Antworten reinrutschen)
                        "no thanks", "thanks", "bye", "byebye", "quit", "exit"
                    }

                    _norm = _norm_endphrase(user_text)

                    if (_norm in END_EXIT) or any(_norm.endswith(p) for p in END_EXIT):
                        logger.info("[pi] follow-up: end phrase → back to wakeword")
                        say(MODEL_BYEBYE)
                        logger.info("[piper] say %s", MODEL_BYEBYE)
                        _led_set_state_safe(CogletState.AWAIT_WAKEWORD)
                        break

                    # LLM
                    _conv.add_user(user_text)
                    anim_think_start()
                    try:
                        reply = llm_chat_once(user_text) or ""
                    finally:
                        anim_think_stop()
                    _conv.add_assistant(reply)

                    # TTS
                    speak_and_back_to_idle(reply, rec, kw)
                    logger.info("[tts] done")

            if exit_requested:
                break

            # regular cooldown and back to the wakeword
            logger.info("[pi] cooldown %ss & flush ring", os.getenv("COOLDOWN_AFTER_TTS_S", "1.0"))
            _led_set_state_safe(CogletState.AWAIT_WAKEWORD)

    except KeyboardInterrupt:
        logger.info("[pi] interrupted by user.")
        say(MODEL_BYEBYE)
        _shutdown_event.set()
    finally:
        if face_tracker_cleanup is not None:
            try:
                face_tracker_cleanup()
            except Exception as exc:
                logger.debug("Face tracking cleanup failed: %s", exc)
        try:
            _restore_neutral_pose_and_close_lid()
        except Exception as exc:
            logger.debug("Neutral pose restore during shutdown failed: %s", exc)
        _cleanup_servo_hardware(servo_setup)
        try:
            rec.stop()
        except Exception:
            pass
        try:
            _led_set_state_safe(CogletState.OFF)
        except Exception:
            pass

if __name__ == "__main__":
    main()
