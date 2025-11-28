"""Tests for the PCA9685 servo control implementation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware.pca9685_servo import Servo, ServoConfig


class DummyChannel:
    def __init__(self) -> None:
        self.duty_cycle = 0


@pytest.fixture()
def default_config() -> ServoConfig:
    return ServoConfig(
        min_angle_deg=-45.0,
        max_angle_deg=45.0,
        min_pulse_us=600.0,
        max_pulse_us=2400.0,
        max_speed_deg_per_s=90.0,
        max_accel_deg_per_s2=360.0,
        deadzone_deg=1.0,
        neutral_deg=0.0,
        pwm_frequency_hz=50.0,
    )


def test_pulse_width_mapping(default_config: ServoConfig) -> None:
    channel = DummyChannel()
    fast_config = ServoConfig(
        min_angle_deg=default_config.min_angle_deg,
        max_angle_deg=default_config.max_angle_deg,
        min_pulse_us=default_config.min_pulse_us,
        max_pulse_us=default_config.max_pulse_us,
        max_speed_deg_per_s=10_000.0,
        max_accel_deg_per_s2=10_000.0,
        deadzone_deg=default_config.deadzone_deg,
        neutral_deg=default_config.neutral_deg,
        pwm_frequency_hz=default_config.pwm_frequency_hz,
    )
    servo = Servo(channel, config=fast_config)

    for angle, pulse in [
        (
            fast_config.max_angle_deg,
            fast_config.max_pulse_us,
        ),
        (
            fast_config.min_angle_deg,
            fast_config.min_pulse_us,
        ),
        (0.0, (fast_config.min_pulse_us + fast_config.max_pulse_us) / 2),
    ]:
        servo.move_to(angle)
        for _ in range(50):
            servo.update(0.02)
            if abs(servo.angle_deg - angle) <= 1e-3:
                break
        assert abs(servo.angle_deg - angle) <= 1e-2
        period_us = 1_000_000.0 / fast_config.pwm_frequency_hz
        expected_duty = int(round((pulse / period_us) * 0xFFFF))
        assert channel.duty_cycle == expected_duty


def test_deadzone_stops_micro_movements(default_config: ServoConfig) -> None:
    channel = DummyChannel()
    servo = Servo(channel, config=default_config)

    servo.move_to(0.4)
    servo.update(0.05)
    assert servo.angle_deg == pytest.approx(0.0, abs=1e-6)
    assert servo.target_deg == pytest.approx(0.0, abs=1e-6)


def test_speed_and_accel_limits(default_config: ServoConfig) -> None:
    channel = DummyChannel()
    servo = Servo(channel, config=default_config)

    servo.move_to(30.0)
    servo.update(0.1)
    # max acceleration = 360 deg/s^2, so after 0.1 s velocity <= 36 deg/s
    assert abs(servo.velocity_deg_per_s) <= 36.0 + 1e-6
    # angle moved should be velocity * dt
    assert abs(servo.angle_deg) <= 3.6 + 1e-6

    # subsequent update should not exceed max speed of 90 deg/s
    servo.update(0.1)
    assert abs(servo.velocity_deg_per_s) <= default_config.max_speed_deg_per_s + 1e-6
