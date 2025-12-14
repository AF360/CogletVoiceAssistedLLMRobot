"""Tests for the servo presets inspired by Will Cogley."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware.pca9685_servo import Servo
from hardware.servo_presets import (
    POSE_REST,
    SERVO_LAYOUT_V1,
    apply_pose,
    get_pose,
    iter_face_tracking_servos,
)


class DummyChannel:
    def __init__(self) -> None:
        self.duty_cycle = 0


def _make_servo(name: str) -> Servo:
    definition = SERVO_LAYOUT_V1[name]
    return Servo(DummyChannel(), config=definition.config)


def test_pose_lookup_unknown_defaults_to_rest() -> None:
    pose = get_pose("pose_does_not_exist")
    assert pose == POSE_REST


def test_apply_pose_sets_targets_for_available_servos() -> None:
    servos = {
        "NRL": _make_servo("NRL"),
        "LID": _make_servo("LID"),
    }
    apply_pose(servos, "pose_thinking_1")
    assert servos["NRL"].target_deg == pytest.approx(130.0)
    assert servos["LID"].target_deg == pytest.approx(70.0)


def test_servo_layout_contains_reference_channels() -> None:
    assert SERVO_LAYOUT_V1["EYL"].channel == 0
    assert SERVO_LAYOUT_V1["EYR"].channel == 1
    assert SERVO_LAYOUT_V1["NRL"].channel == 4
    assert SERVO_LAYOUT_V1["LID"].config.neutral_deg == pytest.approx(130.0)


def test_servo_layout_contains_wheel_servos() -> None:
    assert SERVO_LAYOUT_V1["LWH"].channel == 8
    assert SERVO_LAYOUT_V1["RWH"].channel == 9
    assert SERVO_LAYOUT_V1["LWH"].config.max_speed_deg_per_s == pytest.approx(100.0)
    assert SERVO_LAYOUT_V1["RWH"].config.max_accel_deg_per_s2 == pytest.approx(25.0)


def test_iter_face_tracking_servos_orders_known_servos() -> None:
    servos = {
        "EYL": _make_servo("EYL"),
        "NRL": _make_servo("NRL"),
        "NPT": _make_servo("NPT"),
    }
    ordered = list(iter_face_tracking_servos(servos))
    assert ordered[0] is servos["EYL"]
    assert ordered[-1] is servos["NPT"]
