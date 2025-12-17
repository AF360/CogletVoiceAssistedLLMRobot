"""Helfer zum Laden und Anwenden von Servo-Kalibrierungen."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Dict, Iterable, Tuple

from hardware.pca9685_servo import ServoConfig

__all__ = [
    "ServoCalibration",
    "apply_calibration_to_config",
    "load_servo_calibration",
    "merge_config_with_calibration",
]


@dataclass(frozen=True)
class ServoCalibration:
    """Describe min/max/neutral angles of a servo channel."""

    channel: int
    min_deg: float
    max_deg: float
    start_deg: float
    stop_deg: float | None

    @property
    def clamped_start(self) -> float:
        """Clamp the neutral angle into the allowed range."""
        return max(self.min_deg, min(self.max_deg, self.start_deg))

    @property
    def clamped_stop(self) -> float:
        """Clamp the stop angle into the allowed range (if defined)."""
        if self.stop_deg is None:
            return self.clamped_start
        return max(self.min_deg, min(self.max_deg, self.stop_deg))


def _default_calibration_paths() -> Tuple[Path, ...]:
    """Return default search paths for the calibration file."""
    module_root = Path(__file__).resolve()
    repo_root = module_root.parent.parent.parent
    pi_side_root = module_root.parent.parent
    hardware_root = module_root.parent
    base_dirs = (hardware_root, Path.cwd(), repo_root, pi_side_root)
    names = ("servo-calibration.json", "servo_calibration.json")

    seen = set()
    paths = []
    for base_dir in base_dirs:
        for name in names:
            candidate = base_dir / name
            if candidate in seen:
                continue
            seen.add(candidate)
            paths.append(candidate)
    return tuple(paths)


def _parse_entry(raw: object, *, logger: logging.Logger | None) -> ServoCalibration | None:
    if not isinstance(raw, dict):
        return None
    try:
        channel = int(raw["channel"])
        min_deg = float(raw["min_deg"])
        max_deg = float(raw["max_deg"])
        start_deg = float(raw["start_deg"])
        stop_raw = raw.get("stop_deg")
    except (KeyError, TypeError, ValueError) as exc:
        if logger:
            logger.warning("Ignoring invalid servo calibration entry %r: %s", raw, exc)
        return None
    stop_deg: float | None
    try:
        stop_deg = float(stop_raw) if stop_raw is not None else None
    except (TypeError, ValueError) as exc:
        if logger:
            logger.warning("Ignoring stop_deg for channel %d: %s", channel, exc)
        stop_deg = None
    if min_deg >= max_deg:
        if logger:
            logger.warning(
                "Ignoring servo calibration for channel %d: min_deg %.1f must be smaller than max_deg %.1f",
                channel,
                min_deg,
                max_deg,
            )
        return None
    return ServoCalibration(
        channel=channel,
        min_deg=min_deg,
        max_deg=max_deg,
        start_deg=start_deg,
        stop_deg=stop_deg,
    )


def load_servo_calibration(
    logger: logging.Logger | None = None, *, search_paths: Iterable[Path] | None = None
) -> Tuple[Dict[int, ServoCalibration], Path | None]:
    """Load calibration data if present."""
    calibration_map: Dict[int, ServoCalibration] = {}
    paths = tuple(search_paths) if search_paths is not None else _default_calibration_paths()

    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            if logger:
                logger.warning("Failed to read servo calibration from %s: %s", path, exc)
            continue

        raw_entries = data.get("servos") if isinstance(data, dict) else None
        if not isinstance(raw_entries, list):
            if logger:
                logger.warning("Servo calibration in %s ignored: missing 'servos' list", path)
            continue

        for raw in raw_entries:
            entry = _parse_entry(raw, logger=logger)
            if entry is None:
                continue
            calibration_map[entry.channel] = entry

        if calibration_map:
            if logger:
                logger.info(
                    "Loaded servo calibration from %s for channels %s",
                    path,
                    sorted(calibration_map.keys()),
                )
            return calibration_map, path
    return calibration_map, None


def apply_calibration_to_config(base: ServoConfig, calibration: ServoCalibration) -> ServoConfig:
    """Apply calibration values to a servo configuration.
    
    IMPORTANT: This function assumes the calibration was performed using the
    default reference frame of the 'pca9685_servo_calibration' tool:
      -90 deg -> 500 us
      +90 deg -> 2500 us
    
    It maps the calibrated min/max angles to new min/max pulse widths
    so that 0 degrees in calibration remains 0 degrees (1500us) in runtime.
    """
    
    # Reference frame of the calibration tool
    REF_MIN_ANGLE = -90.0
    REF_MAX_ANGLE = 90.0
    REF_MIN_PULSE = 500.0
    REF_MAX_PULSE = 2500.0
    
    ref_span_angle = REF_MAX_ANGLE - REF_MIN_ANGLE
    ref_span_pulse = REF_MAX_PULSE - REF_MIN_PULSE
    us_per_deg = ref_span_pulse / ref_span_angle  # ~11.11 us/deg

    # Calculate the pulse width for the calibrated angles
    # Pulse = 500 + (Angle - (-90)) * us_per_deg
    def angle_to_pulse(angle: float) -> float:
        return REF_MIN_PULSE + (angle - REF_MIN_ANGLE) * us_per_deg

    new_min_pulse = angle_to_pulse(calibration.min_deg)
    new_max_pulse = angle_to_pulse(calibration.max_deg)

    neutral = calibration.clamped_start
    
    return ServoConfig(
        min_angle_deg=calibration.min_deg,
        max_angle_deg=calibration.max_deg,
        min_pulse_us=new_min_pulse,   # <--- Updated Pulse Limit
        max_pulse_us=new_max_pulse,   # <--- Updated Pulse Limit
        max_speed_deg_per_s=base.max_speed_deg_per_s,
        max_accel_deg_per_s2=base.max_accel_deg_per_s2,
        deadzone_deg=base.deadzone_deg,
        neutral_deg=neutral,
        invert=base.invert,
        pwm_frequency_hz=base.pwm_frequency_hz,
    )


def merge_config_with_calibration(
    env_config: ServoConfig, calibration: ServoCalibration | None
) -> ServoConfig:
    """Apply calibration after environment overrides."""
    if calibration is None:
        return env_config
    return apply_calibration_to_config(env_config, calibration)
    
