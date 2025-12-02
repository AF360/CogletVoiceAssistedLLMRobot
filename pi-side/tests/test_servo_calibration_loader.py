import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import hardware.servo_calibration as servo_calibration

from hardware.pca9685_servo import ServoConfig
from hardware.servo_calibration import ServoCalibration, apply_calibration_to_config, load_servo_calibration


def test_apply_calibration_to_config_overrides_angles_and_neutral() -> None:
    base = ServoConfig(
        min_angle_deg=0.0,
        max_angle_deg=180.0,
        min_pulse_us=500.0,
        max_pulse_us=2500.0,
        max_speed_deg_per_s=200.0,
        max_accel_deg_per_s2=400.0,
        deadzone_deg=0.5,
        neutral_deg=90.0,
        invert=False,
        pwm_frequency_hz=50.0,
    )
    calibration = ServoCalibration(channel=2, min_deg=-30.0, max_deg=45.0, start_deg=100.0, stop_deg=None)

    calibrated = apply_calibration_to_config(base, calibration)

    assert calibrated.min_angle_deg == -30.0
    assert calibrated.max_angle_deg == 45.0
    assert calibrated.neutral_deg == 45.0
    assert calibrated.min_pulse_us == base.min_pulse_us
    assert calibrated.max_pulse_us == base.max_pulse_us
    assert calibrated.max_speed_deg_per_s == base.max_speed_deg_per_s
    assert calibrated.max_accel_deg_per_s2 == base.max_accel_deg_per_s2
    assert calibrated.deadzone_deg == base.deadzone_deg
    assert calibrated.invert is base.invert
    assert calibrated.pwm_frequency_hz == base.pwm_frequency_hz


def test_load_servo_calibration_reads_first_valid_file(tmp_path) -> None:
    data = {
        "servos": [
            {"channel": 0, "min_deg": -10.0, "max_deg": 20.0, "start_deg": 5.0, "stop_deg": 10.0},
            {"channel": 1, "min_deg": -5.0, "max_deg": 25.0, "start_deg": 0.0, "stop_deg": -2.0},
        ]
    }
    calibration_path = tmp_path / "servo-calibration.json"
    calibration_path.write_text(json.dumps(data), encoding="utf-8")

    mapping, used_path = load_servo_calibration(search_paths=[calibration_path])

    assert used_path == calibration_path
    assert set(mapping.keys()) == {0, 1}
    assert mapping[0].min_deg == -10.0
    assert mapping[0].max_deg == 20.0
    assert mapping[0].clamped_start == 5.0
    assert mapping[0].clamped_stop == 10.0
    assert mapping[1].clamped_start == 0.0
    assert mapping[1].clamped_stop == -2.0


def test_load_servo_calibration_ignores_invalid_entries(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING)
    invalid_data = {"servos": [{"channel": 0, "min_deg": 10.0, "max_deg": 5.0, "start_deg": 0.0}]}
    calibration_path = tmp_path / "servo_calibration.json"
    calibration_path.write_text(json.dumps(invalid_data), encoding="utf-8")

    mapping, used_path = load_servo_calibration(logger=logging.getLogger(__name__), search_paths=[calibration_path])

    assert mapping == {}
    assert used_path is None
    assert "min_deg" in caplog.text


def test_default_paths_include_module_directory(tmp_path, monkeypatch) -> None:
    fake_module_path = tmp_path / "repo" / "pi-side" / "hardware" / "servo_calibration.py"
    fake_module_path.parent.mkdir(parents=True)
    fake_module_path.write_text("# dummy module", encoding="utf-8")

    monkeypatch.setattr(servo_calibration, "__file__", str(fake_module_path))

    paths = servo_calibration._default_calibration_paths()
    expected = fake_module_path.parent / "servo-calibration.json"
    alternate = fake_module_path.parent / "servo_calibration.json"

    assert expected in paths or alternate in paths
    
