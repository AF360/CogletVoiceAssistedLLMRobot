"""Tests for the high-level face tracking helpers."""

from __future__ import annotations

import time

import pytest

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware.pca9685_servo import Servo, ServoConfig
from hardware.face_tracker import FaceTracker, FaceTrackingConfig, FaceTrackingServos
from hardware.grove_vision_ai import FaceDetectionBox


class DummyChannel:
    def __init__(self) -> None:
        self.duty_cycle = 0


def make_servo(*, config: ServoConfig | None = None) -> Servo:
    config = config or ServoConfig(
        min_angle_deg=-45.0,
        max_angle_deg=45.0,
        min_pulse_us=600.0,
        max_pulse_us=2400.0,
        max_speed_deg_per_s=360.0,
        max_accel_deg_per_s2=720.0,
        deadzone_deg=0.5,
        neutral_deg=0.0,
        pwm_frequency_hz=50.0,
    )
    return Servo(DummyChannel(), config=config)


def test_face_tracker_adjusts_targets() -> None:
    eyes = (make_servo(),)
    yaw = make_servo()
    pitch = make_servo()
    tracker = FaceTracker(
        client=object(),
        servos=FaceTrackingServos(eyes=eyes, yaw=yaw, pitch=pitch),
        config=FaceTrackingConfig(coordinates_are_center=True),
    )
    box = FaceDetectionBox(x=130.0, y=120.0, width=10.0, height=10.0, score=0.9)
    tracker._handle_detection([box], timestamp=time.monotonic())
    assert eyes[0].target_deg > 0.0
    assert yaw.target_deg > 0.0
    assert pitch.target_deg < 0.0


def test_face_tracker_moves_left_of_center() -> None:
    eyes = (make_servo(),)
    yaw = make_servo()
    pitch = make_servo()
    tracker = FaceTracker(
        client=object(),
        servos=FaceTrackingServos(eyes=eyes, yaw=yaw, pitch=pitch),
        config=FaceTrackingConfig(coordinates_are_center=True),
    )
    box = FaceDetectionBox(x=90.0, y=100.0, width=10.0, height=10.0, score=0.8)
    tracker._handle_detection([box], timestamp=time.monotonic())
    assert eyes[0].target_deg < 0.0
    assert yaw.target_deg < 0.0
    assert pitch.target_deg == pytest.approx(0.0)


def test_face_tracker_resets_to_neutral() -> None:
    eyes = (make_servo(),)
    tracker = FaceTracker(
        client=object(),
        servos=FaceTrackingServos(eyes=eyes),
        config=FaceTrackingConfig(neutral_timeout_s=0.0, coordinates_are_center=True),
    )
    box = FaceDetectionBox(x=130.0, y=100.0, width=10.0, height=10.0, score=0.5)
    tracker._handle_detection([box], timestamp=time.monotonic())
    assert eyes[0].target_deg != pytest.approx(0.0)
    tracker._handle_missing_detection(time.monotonic() + 0.1)
    assert eyes[0].target_deg == pytest.approx(0.0)


def test_wheels_follow_after_delay() -> None:
    eye_config = ServoConfig(
        min_angle_deg=30.0,
        max_angle_deg=150.0,
        min_pulse_us=600.0,
        max_pulse_us=2400.0,
        max_speed_deg_per_s=360.0,
        max_accel_deg_per_s2=720.0,
        deadzone_deg=0.5,
        neutral_deg=90.0,
        pwm_frequency_hz=50.0,
    )
    wheel_config = ServoConfig(
        min_angle_deg=30.0,
        max_angle_deg=150.0,
        min_pulse_us=600.0,
        max_pulse_us=2400.0,
        max_speed_deg_per_s=200.0,
        max_accel_deg_per_s2=100.0,
        deadzone_deg=0.5,
        neutral_deg=90.0,
        pwm_frequency_hz=50.0,
    )
    eyes = (make_servo(config=eye_config), make_servo(config=eye_config))
    wheels = (make_servo(config=wheel_config), make_servo(config=wheel_config))
    cfg = FaceTrackingConfig(
        coordinates_are_center=True,
        eye_deadzone_px=0.0,
        yaw_deadzone_px=999.0,
        pitch_deadzone_px=999.0,
        eye_gain_deg_per_px=1.0,
        wheel_deadzone_deg=1.0,
        wheel_follow_delay_s=0.0,
        wheel_input_min_deg=30.0,
        wheel_input_max_deg=150.0,
        wheel_output_min_deg=80.0,
        wheel_output_max_deg=100.0,
    )
    tracker = FaceTracker(
        client=object(),
        servos=FaceTrackingServos(eyes=eyes, wheels=wheels),
        config=cfg,
    )
    box = FaceDetectionBox(x=cfg.frame_center_x + 40.0, y=cfg.frame_center_y, width=10.0, height=10.0, score=0.9)
    tracker._handle_detection([box], timestamp=time.monotonic())
    assert wheels[0].target_deg > wheels[0].config.neutral_deg


def test_wheels_reset_to_neutral_inside_deadzone() -> None:
    eye_config = ServoConfig(
        min_angle_deg=30.0,
        max_angle_deg=150.0,
        min_pulse_us=600.0,
        max_pulse_us=2400.0,
        max_speed_deg_per_s=360.0,
        max_accel_deg_per_s2=720.0,
        deadzone_deg=0.5,
        neutral_deg=90.0,
        pwm_frequency_hz=50.0,
    )
    wheel_config = ServoConfig(
        min_angle_deg=30.0,
        max_angle_deg=150.0,
        min_pulse_us=600.0,
        max_pulse_us=2400.0,
        max_speed_deg_per_s=200.0,
        max_accel_deg_per_s2=100.0,
        deadzone_deg=0.5,
        neutral_deg=90.0,
        pwm_frequency_hz=50.0,
    )
    eyes = (make_servo(config=eye_config), make_servo(config=eye_config))
    wheels = (make_servo(config=wheel_config), make_servo(config=wheel_config))
    cfg = FaceTrackingConfig(
        coordinates_are_center=True,
        eye_deadzone_px=0.0,
        wheel_deadzone_deg=1.0,
        wheel_follow_delay_s=0.0,
        wheel_input_min_deg=30.0,
        wheel_input_max_deg=150.0,
        wheel_output_min_deg=80.0,
        wheel_output_max_deg=100.0,
    )
    tracker = FaceTracker(
        client=object(),
        servos=FaceTrackingServos(eyes=eyes, wheels=wheels),
        config=cfg,
    )
    off_center = FaceDetectionBox(x=cfg.frame_center_x + 40.0, y=cfg.frame_center_y, width=10.0, height=10.0, score=0.9)
    tracker._handle_detection([off_center], timestamp=time.monotonic())
    assert wheels[0].target_deg != pytest.approx(wheels[0].config.neutral_deg)
    centered = FaceDetectionBox(x=cfg.frame_center_x, y=cfg.frame_center_y, width=10.0, height=10.0, score=0.9)
    tracker._handle_detection([centered], timestamp=time.monotonic())
    assert wheels[0].target_deg == pytest.approx(wheels[0].config.neutral_deg)
