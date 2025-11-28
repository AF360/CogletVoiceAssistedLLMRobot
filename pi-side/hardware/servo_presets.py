"""
Servo layout and pose presets for Coglet.

Based in part on the servo mapping and neutral/limit angles from
Will Cogley's face-tracking mini bot reference code (main.py),
used under the Creative Commons Attribution-NonCommercial-ShareAlike
4.0 International License (CC BY-NC-SA 4.0).

Original work: https://www.willcogley.com/
License: https://creativecommons.org/licenses/by-nc-sa/4.0/
Modifications and extensions for Coglet by Andreas Fatum, 2025.
"""


from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping

from hardware.pca9685_servo import Servo, ServoConfig

__all__ = [
    "ServoDefinition",
    "SERVO_LAYOUT_V1",
    "POSE_CALIBRATE",
    "POSE_REST",
    "POSE_THINKING_1",
    "POSE_CURIOUS_2",
    "POSE_MAP",
    "get_pose",
    "apply_pose",
    "iter_face_tracking_servos",
    "TRACKING_SERVO_NAMES",
    "PERSONALITY_SERVO_NAMES",
]

TRACKING_SERVO_NAMES: tuple[str, ...] = ("EYL", "EYR", "NPT", "LWH", "RWH")
PERSONALITY_SERVO_NAMES: tuple[str, ...] = ("NRL", "MOU", "EAL", "EAR")
# Notes:
# - LID is controlled exclusively by the EyelidController
# - NRL is a head-roll servo (head to shoulder) on this Coglet hardware,
#   not a true yaw servo, and is only used for personality poses.


@dataclass(frozen=True)
class ServoDefinition:
    """Definition of a servo in the Coglet setup."""

    channel: int
    config: ServoConfig


# Mapping of PCA9685 channels (SERVO_LAYOUT_V1):
#
#   0: EYL (left eye)
#   1: EYR (right eye)
#   2: LID (lid/blink)
#   3: NPT (head up/down)
#   4: NRL (head left/right)
#   5: MOU (mouth)
#   6: EAL (left ear)
#   7: EAR (right ear)
#   8: LWH (left wheel)
#   9: RWH (right wheel)

SERVO_LAYOUT_V1: Dict[str, ServoDefinition] = {
    "LWH": ServoDefinition(
        channel=8,
        config=ServoConfig(
            min_angle_deg=30.0,
            max_angle_deg=120.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=100.0,
            max_accel_deg_per_s2=25.0,
            deadzone_deg=1.0,
            neutral_deg=90.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "RWH": ServoDefinition(
        channel=9,
        config=ServoConfig(
            min_angle_deg=30.0,
            max_angle_deg=120.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=100.0,
            max_accel_deg_per_s2=25.0,
            deadzone_deg=1.0,
            neutral_deg=90.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "EYL": ServoDefinition(
        channel=0,
        config=ServoConfig(
            min_angle_deg=30.0,
            max_angle_deg=150.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=200.0,
            max_accel_deg_per_s2=1000.0,
            deadzone_deg=0.8,
            neutral_deg=90.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "EYR": ServoDefinition(
        channel=1,
        config=ServoConfig(
            min_angle_deg=30.0,
            max_angle_deg=150.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=250.0,
            max_accel_deg_per_s2=1000.0,
            deadzone_deg=0.8,
            neutral_deg=90.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "LID": ServoDefinition(
        channel=2,
        config=ServoConfig(
            min_angle_deg=30.0,
            max_angle_deg=160.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=50000.0,
            max_accel_deg_per_s2=50000.0,
            deadzone_deg=1.0,
            neutral_deg=130.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "NPT": ServoDefinition(
        channel=3,
        config=ServoConfig(
            min_angle_deg=1.0,
            max_angle_deg=120.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=600.0,
            max_accel_deg_per_s2=400.0,
            deadzone_deg=1.0,
            neutral_deg=50.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "NRL": ServoDefinition(
        channel=4,
        config=ServoConfig(
            min_angle_deg=30.0,
            max_angle_deg=150.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=600.0,
            max_accel_deg_per_s2=400.0,
            deadzone_deg=1.2,
            neutral_deg=90.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "MOU": ServoDefinition(
        channel=5,
        config=ServoConfig(
            min_angle_deg=10.0,
            max_angle_deg=150.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=50000.0,
            max_accel_deg_per_s2=10000.0,
            deadzone_deg=1.0,
            neutral_deg=170.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "EAL": ServoDefinition(
        channel=6,
        config=ServoConfig(
            min_angle_deg=60.0,
            max_angle_deg=150.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=250.0,
            max_accel_deg_per_s2=200.0,
            deadzone_deg=1.0,
            neutral_deg=130.0,
            pwm_frequency_hz=50.0,
        ),
    ),
    "EAR": ServoDefinition(
        channel=7,
        config=ServoConfig(
            min_angle_deg=30.0,
            max_angle_deg=120.0,
            min_pulse_us=600.0,
            max_pulse_us=2400.0,
            max_speed_deg_per_s=500.0,
            max_accel_deg_per_s2=200.0,
            deadzone_deg=1.0,
            neutral_deg=70.0,
            pwm_frequency_hz=50.0,
        ),
    ),
}

POSE_CALIBRATE = {
    "NRL": 90.0,
    "MOU": 170.0,
    "LID": 110.0,
    "EAL": 90.0,
    "EAR": 90.0,
}

POSE_REST = {
    "NRL": 90.0,
    "MOU": 170.0,
    "LID": 130.0,
    "EAL": 130.0,
    "EAR": 70.0,
}

POSE_THINKING_1 = {
    "NRL": 130.0,
    "MOU": 150.0,
    "LID": 70.0,
    "EAL": 150.0,
    "EAR": 120.0,
}

POSE_CURIOUS_2 = {
    "NRL": 80.0,
    "MOU": 160.0,
    "LID": 140.0,
    "EAL": 60.0,
    "EAR": 60.0,
}

POSE_MAP: Dict[str, Mapping[str, float]] = {
    "pose_calibrate": POSE_CALIBRATE,
    "pose_rest": POSE_REST,
    "pose_thinking_1": POSE_THINKING_1,
    "pose_curious_2": POSE_CURIOUS_2,
}


def get_pose(name: str) -> Mapping[str, float]:
    """Return a pose; unknown names fall back to ``pose_rest``."""

    return POSE_MAP.get(name, POSE_REST)


def apply_pose(servos: Mapping[str, Servo], pose: str | Mapping[str, float]) -> None:
    """Set target angles of the provided servos for the desired pose."""

    pose_data = get_pose(pose) if isinstance(pose, str) else pose
    for name, angle in pose_data.items():
        servo = servos.get(name)
        if servo is not None:
            servo.move_to(angle)


def iter_face_tracking_servos(servos: Mapping[str, Servo]) -> Iterable[Servo]:
    """Return the servos relevant for face tracking.

    On the current Coglet hardware:
    - EYL/EYR: eyes
    - NPT: head nod (pitch)
    - LWH/RWH: wheels for horizontal rotation
    - NRL: head roll (head to shoulder) -> personality only, not used for tracking.
    """

    for key in TRACKING_SERVO_NAMES:
        servo = servos.get(key)
        if servo is not None:
            yield servo
