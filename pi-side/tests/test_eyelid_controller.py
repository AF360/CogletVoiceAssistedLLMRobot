import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware.pca9685_servo import ServoConfig  # noqa: E402
from hardware.eyelid_controller import EyelidController  # noqa: E402


class FakeServo:
    def __init__(self, neutral: float = 120.0) -> None:
        self.config = ServoConfig(neutral_deg=neutral)
        self.target_deg = neutral
        self.angle_deg = neutral
        self.history: list[float] = []

    def move_to(self, angle_deg: float) -> None:
        self.target_deg = angle_deg

    def update(self, dt: float) -> None:  # noqa: ARG002
        self.angle_deg = self.target_deg
        self.history.append(self.angle_deg)


def test_manual_blink_reaches_closed_and_reopens() -> None:
    servo = FakeServo(neutral=120.0)
    controller = EyelidController(
        servo,
        open_angle_deg=120.0,
        closed_angle_deg=60.0,
        blink_interval_min_s=10.0,
        blink_interval_max_s=10.0,
        blink_close_s=0.005,
        blink_hold_s=0.0,
        blink_open_s=0.005,
    )

    controller.set_open()
    controller.blink_once()
    time.sleep(0.05)
    controller.shutdown()

    assert min(servo.history) <= 60.0 + 1e-3
    assert servo.angle_deg == pytest.approx(120.0, abs=1e-6)


def test_modes_disable_auto_blinking() -> None:
    servo = FakeServo(neutral=100.0)
    controller = EyelidController(
        servo,
        open_angle_deg=100.0,
        closed_angle_deg=50.0,
        sleep_fraction=0.5,
        blink_interval_min_s=0.001,
        blink_interval_max_s=0.002,
        blink_close_s=0.0,
        blink_hold_s=0.0,
        blink_open_s=0.0,
    )

    controller.set_mode("closed")
    time.sleep(0.01)
    assert servo.angle_deg == pytest.approx(50.0, abs=1e-6)

    controller.set_mode("sleep")
    time.sleep(0.01)
    expected_sleep = 75.0
    assert servo.angle_deg == pytest.approx(expected_sleep, abs=1e-6)

    controller.set_mode("auto")
    time.sleep(0.03)
    controller.shutdown()

    assert servo.angle_deg == pytest.approx(100.0, abs=1e-6)
    assert all(value >= 50.0 for value in servo.history)


def test_override_suspends_blinking_temporarily() -> None:
    servo = FakeServo(neutral=120.0)
    controller = EyelidController(
        servo,
        open_angle_deg=130.0,
        closed_angle_deg=70.0,
        blink_interval_min_s=0.001,
        blink_interval_max_s=0.002,
        blink_close_s=0.0,
        blink_hold_s=0.0,
        blink_open_s=0.0,
    )

    controller.set_override(90.0, duration_s=0.02)
    time.sleep(0.01)
    history_after_override = list(servo.history)

    assert servo.angle_deg == pytest.approx(90.0, abs=1e-6)

    time.sleep(0.05)
    controller.shutdown()

    resumed_history = servo.history[len(history_after_override) :]
    assert resumed_history, "Expected the controller to resume blinking after override"
    assert any(value != pytest.approx(90.0, abs=1e-6) for value in resumed_history)
