"""Servo control via the PCA9685 PWM driver.

The implementation is based on the MicroPython servo class but adapted for
CPython. Core functionality:

* Configurable pulse width (min/max) and angle range.
* Speed and acceleration limits to achieve smooth movement.
* Deadzone to suppress minimal position changes.
* Output PWM values to a PCA9685 channel (e.g., from
  ``adafruit-circuitpython-pca9685``).

The class is intentionally generic so it can be used in tests with simulated
channels. Only a ``duty_cycle`` attribute (0..65535) is required on the provided
channel.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
from typing import Protocol

__all__ = ["Servo", "ServoConfig", "PCA9685ChannelProtocol"]


class PCA9685ChannelProtocol(Protocol):
    """Minimal protocol for a PCA9685 channel.

    The class only uses the ``duty_cycle`` attribute, allowing tests to supply a
    fake object with the same interface.
    """

    duty_cycle: int


@dataclass(frozen=True)
class ServoConfig:
    """Configurable parameters for servo control."""

    min_angle_deg: float = -90.0
    max_angle_deg: float = 90.0
    min_pulse_us: float = 500.0
    max_pulse_us: float = 2500.0
    max_speed_deg_per_s: float = 360.0
    max_accel_deg_per_s2: float = 720.0
    deadzone_deg: float = 0.5
    neutral_deg: float = 0.0
    invert: bool = False
    pwm_frequency_hz: float = 50.0

    def __post_init__(self) -> None:
        if self.min_angle_deg >= self.max_angle_deg:
            raise ValueError("min_angle_deg muss kleiner als max_angle_deg sein")
        if self.min_pulse_us >= self.max_pulse_us:
            raise ValueError("min_pulse_us muss kleiner als max_pulse_us sein")
        if self.max_speed_deg_per_s <= 0.0:
            raise ValueError("max_speed_deg_per_s muss positiv sein")
        if self.max_accel_deg_per_s2 <= 0.0:
            raise ValueError("max_accel_deg_per_s2 muss positiv sein")
        if self.deadzone_deg < 0.0:
            raise ValueError("deadzone_deg darf nicht negativ sein")
        if self.pwm_frequency_hz <= 0.0:
            raise ValueError("pwm_frequency_hz muss positiv sein")


class Servo:
    """Control a single servo channel via the PCA9685."""

    def __init__(self, channel: PCA9685ChannelProtocol, *, config: ServoConfig | None = None) -> None:
        self._lock = threading.RLock()
        self._channel = channel
        self.config = config or ServoConfig()

        neutral = self._clamp_angle(self.config.neutral_deg)
        self._target_deg = neutral
        self._angle_deg = neutral
        self._velocity_deg_per_s = 0.0
        self._last_pulse = self._angle_to_pulse(self._angle_deg)
        self._write_pwm(self._last_pulse)

    # ---------------------------- Public API ----------------------------
    @property
    def angle_deg(self) -> float:
        """Current servo angle in degrees."""

        with self._lock:
            return self._angle_deg

    @property
    def target_deg(self) -> float:
        """Target angle set via ``move_to``."""

        with self._lock:
            return self._target_deg

    @property
    def velocity_deg_per_s(self) -> float:
        """Current angular velocity."""

        with self._lock:
            return self._velocity_deg_per_s

    def move_to(self, angle_deg: float) -> None:
        """Set a new target angle.

        The angle is clamped to the allowed range [``min_angle_deg``,
        ``max_angle_deg``].
        """

        with self._lock:
            self._target_deg = self._clamp_angle(angle_deg)

    def nudge(self, delta_deg: float) -> None:
        """Shift the target angle relative to the current value."""

        with self._lock:
            self.move_to(self._target_deg + delta_deg)

    def reset(self) -> None:
        """Reset servo and target to the neutral angle."""

        with self._lock:
            neutral = self._clamp_angle(self.config.neutral_deg)
            self._target_deg = neutral
            self._angle_deg = neutral
            self._velocity_deg_per_s = 0.0
            self._apply_output()

    def update(self, dt: float) -> None:
        """Update angle and PWM output based on ``dt`` in seconds."""

        if dt <= 0.0:
            return
        with self._lock:
            target = self._target_deg
            angle_error = target - self._angle_deg
            if abs(angle_error) <= self.config.deadzone_deg:
                self._velocity_deg_per_s = 0.0
                # self._target_deg = self._angle_deg
                return

            desired_velocity = angle_error / dt
            desired_velocity = _clamp(
                desired_velocity,
                -self.config.max_speed_deg_per_s,
                self.config.max_speed_deg_per_s,
            )

            delta_v = desired_velocity - self._velocity_deg_per_s
            max_delta_v = self.config.max_accel_deg_per_s2 * dt
            delta_v = _clamp(delta_v, -max_delta_v, max_delta_v)
            new_velocity = self._velocity_deg_per_s + delta_v
            new_velocity = _clamp(
                new_velocity,
                -self.config.max_speed_deg_per_s,
                self.config.max_speed_deg_per_s,
            )

            new_angle = self._angle_deg + new_velocity * dt

            if math.copysign(1.0, angle_error) != math.copysign(1.0, target - new_angle):
                new_angle = target
                new_velocity = 0.0

            self._angle_deg = self._clamp_angle(new_angle)
            self._velocity_deg_per_s = new_velocity
            self._apply_output()

    # --------------------------- Internals ---------------------------
    def _apply_output(self) -> None:
        pulse = self._angle_to_pulse(self._angle_deg)
        if pulse != self._last_pulse:
            self._write_pwm(pulse)
            self._last_pulse = pulse

    def _write_pwm(self, pulse_us: float) -> None:
        period_us = 1_000_000.0 / self.config.pwm_frequency_hz
        duty_cycle = int(round(_clamp(pulse_us / period_us, 0.0, 1.0) * 0xFFFF))
        self._channel.duty_cycle = duty_cycle

    def _angle_to_pulse(self, angle_deg: float) -> float:
        if self.config.invert:
            angle_deg = self._invert_angle(angle_deg)
        span_angle = self.config.max_angle_deg - self.config.min_angle_deg
        normalized = (angle_deg - self.config.min_angle_deg) / span_angle
        normalized = _clamp(normalized, 0.0, 1.0)
        pulse_span = self.config.max_pulse_us - self.config.min_pulse_us
        return self.config.min_pulse_us + normalized * pulse_span

    def _clamp_angle(self, angle_deg: float) -> float:
        return _clamp(angle_deg, self.config.min_angle_deg, self.config.max_angle_deg)

    def _invert_angle(self, angle_deg: float) -> float:
        min_a = self.config.min_angle_deg
        max_a = self.config.max_angle_deg
        return max_a - (angle_deg - min_a)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
