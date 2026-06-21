#!/usr/bin/env python3
"""Shared physical robot runtime for both Coglet launchers."""
from __future__ import annotations
import importlib.util, logging, os, random, threading, time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from hardware.pca9685_servo import Servo, ServoConfig
from hardware.servo_calibration import (
    ServoCalibration,
    load_servo_calibration,
    merge_config_with_calibration,
)
from hardware.channel_config import parse_channel_list, resolve_channel_list
from hardware.servo_presets import (
    SERVO_LAYOUT_V1,
    PERSONALITY_SERVO_NAMES,
    apply_pose,
)
from hardware.eyelid_controller import EyelidController


try:
    from hardware.xvf_mic import ReSpeakerMic
    _XVF_MIC_AVAILABLE = True
except ImportError:
    _XVF_MIC_AVAILABLE = False


from logging_setup import get_logger, setup_logging
setup_logging(); logger=get_logger()

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


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None: return default
    value=value.strip().lower()
    return default if not value else value in {"1","true","yes","on"}
FACE_TRACKING_ENABLED=_parse_bool(os.getenv("FACE_TRACKING_ENABLED"),True)
FACE_TRACKING_PATROL_INTERVAL_S=float(os.getenv("FACE_TRACKING_TIMEOUT_S","30.0"))
_anim_servos: Dict[str,Servo]={}; _anim_lock=threading.Lock(); _eyelid_controller: EyelidController|None=None
_mouth_thread: threading.Thread|None=None; _mouth_stop_event=threading.Event(); _thinking_thread: threading.Thread|None=None; _thinking_stop_event=threading.Event()
_shutdown_targets_lock=threading.Lock(); _shutdown_servos_by_channel: Dict[int,Servo]={}; _shutdown_calibration: Dict[int,ServoCalibration]={}
_idle_thread: threading.Thread|None=None; _idle_stop_event=threading.Event(); _status_led: Any=None

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


            servo.update(5.0)
            logger.debug("Parking servo channel %d to %.1f° (%s)", channel, target, reason)
        except Exception as exc:
            logger.debug("Parking servo channel %d failed: %s", channel, exc)


    time.sleep(0.6)
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
            targets["EAR"] = right_forward if toggle else right_back

        _eyelids_override_fraction(0.5, duration_s=2.0)


        _drive_anim_targets(targets, 0.9, stop_event)

        if stop_event.is_set():
            break


        toggle = not toggle

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

def set_deep_sleep_led_pulse(phase: float) -> None:
    if _status_led is None: return
    n=(phase+1.0)/2.0; b=.02+n*.15; r=int(255*b); g=int(180*b)
    if r>0 and g==0: g=1
    if r<5: g=r
    try:
        if hasattr(_status_led,"_set_rgb"): _status_led._set_rgb(r,g,0)
        elif hasattr(_status_led,"_pixels") and _status_led._pixels: _status_led._pixels.fill((r,g,0)); _status_led._pixels.show()
    except Exception: pass

def apply_personality_neutral_pose(): _apply_pose_safe(_personality_neutral_targets())
initialize_all_servos=_initialize_all_servos; setup_face_tracking=_setup_face_tracking; initialize_status_led=_initialize_status_led; led_set_state_safe=_led_set_state_safe; eyelids_set_mode=_eyelids_set_mode; restore_neutral_pose_and_close_lid=_restore_neutral_pose_and_close_lid; cleanup_servo_hardware=_cleanup_servo_hardware; start_idle_animation=_start_idle_animation; stop_idle_animation=_stop_idle_animation
