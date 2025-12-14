from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware import pca9685_servo_calibration as calibration


class FakeDriver:
    def __init__(self) -> None:
        self.commands: list[tuple[int, float]] = []

    def set_angle(self, channel: int, angle_deg: float) -> None:
        self.commands.append((channel, angle_deg))

    def close(self) -> None:  # pragma: no cover - nothing to close
        pass


def test_session_updates_angles_and_bounds() -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0, 1], driver, step_deg=10.0)

    assert driver.commands[-1] == (0, 0.0)

    session.process_command("u")
    session.process_command("u")
    session.process_command("D")
    session.process_command("u")
    session.process_command("U")

    entry0 = session.current_entry
    assert entry0.min_deg == pytest.approx(20.0)
    assert entry0.max_deg == pytest.approx(30.0)
    assert entry0.start_deg == pytest.approx(20.0)
    assert entry0.min_set is True
    assert entry0.max_set is True
    assert entry0.start_set is False

    assert session.process_command("n")
    assert driver.commands[-1] == (1, 0.0)

    session.process_command("d")
    session.process_command("D")
    session.process_command("p")
    assert driver.commands[-1] == (0, 20.0)

    results = session.results()
    assert len(results) == 2
    assert results[0].start_deg == pytest.approx(20.0)
    assert results[1].min_deg == pytest.approx(-10.0)


def test_reset_and_step_adjustments() -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0], driver, step_deg=2.0)

    session.process_command("+")
    assert session.step_deg == pytest.approx(3.0)
    session.process_command("-")
    assert session.step_deg == pytest.approx(2.0)

    session.process_command("u")
    session.process_command("D")
    session.process_command("U")
    entry = session.current_entry
    assert entry.min_set and entry.max_set

    session.process_command("x")
    assert entry.min_set is False
    assert entry.max_set is False
    assert entry.start_deg == pytest.approx(0.0)


def test_reset_via_x_restores_defaults() -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0], driver, step_deg=5.0)

    session.process_command("u")
    session.process_command("D")
    session.process_command("u")
    session.process_command("U")

    entry = session.current_entry
    assert entry.min_set and entry.max_set
    assert entry.start_deg == pytest.approx(5.0)

    session.process_command("x")

    assert entry.min_set is False
    assert entry.max_set is False
    assert entry.start_deg == pytest.approx(0.0)
    assert session.current_angle == pytest.approx(0.0)


def test_quit_command_returns_false() -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0], driver, step_deg=2.0)

    assert session.process_command("Q") is False


def test_process_command_sets_start_and_stop() -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0], driver, step_deg=5.0)

    session.process_command("u")
    session.process_command("A")
    session.process_command("u")
    session.process_command("Z")

    entry = session.current_entry
    assert entry.start_deg == pytest.approx(5.0)
    assert entry.start_set is True
    assert entry.stop_deg == pytest.approx(10.0)
    assert entry.stop_set is True


def test_run_interactive_quit_stores_values_and_stops(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0], driver, step_deg=5.0)

    class SequenceKeyReader:
        def __init__(self) -> None:
            self._commands = iter(["u", "D", "u", "U", "Q"])

        def read_key(self) -> str:
            try:
                return next(self._commands)
            except StopIteration:
                return "Q"

    monkeypatch.setattr(calibration, "KeyReader", SequenceKeyReader)

    calibration.run_interactive(session)

    destination = tmp_path / "calibration.json"
    calibration.export_calibration(session.results(), destination)

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["servos"][0]["min_deg"] == pytest.approx(5.0)
    captured = capsys.readouterr().out
    assert "Q" in captured


def test_run_interactive_highlights_saved_values(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0], driver, step_deg=5.0)

    class SequenceKeyReader:
        def __init__(self) -> None:
            self._commands = iter(["u", "D", "u", "U", "Q"])

        def read_key(self) -> str:
            try:
                return next(self._commands)
            except StopIteration:
                return "Q"

    monkeypatch.setattr(calibration, "KeyReader", SequenceKeyReader)

    calibration.run_interactive(session)

    captured = capsys.readouterr().out
    assert "\033[33m5.0°\033[0m" in captured
    assert "\033[33m10.0°\033[0m" in captured


def test_run_interactive_highlights_start_and_stop(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession([0], driver, step_deg=5.0)

    class SequenceKeyReader:
        def __init__(self) -> None:
            self._commands = iter(["u", "A", "u", "Z", "Q"])

        def read_key(self) -> str:
            try:
                return next(self._commands)
            except StopIteration:
                return "Q"

    monkeypatch.setattr(calibration, "KeyReader", SequenceKeyReader)

    calibration.run_interactive(session)

    captured = capsys.readouterr().out
    assert "\033[33m5.0°\033[0m" in captured
    assert "\033[33m10.0°\033[0m" in captured


def test_parse_channels_handles_ranges_and_duplicates() -> None:
    assert calibration.parse_channels("0-2,2,4") == [0, 1, 2, 4]
    assert calibration.parse_channels("3-1") == [3, 2, 1]


def test_export_calibration(tmp_path: Path) -> None:
    entries = [
        calibration.ServoCalibrationEntry(channel=0, min_deg=-90.0, max_deg=90.0, start_deg=0.0, stop_deg=0.0),
        calibration.ServoCalibrationEntry(channel=1, min_deg=-45.0, max_deg=60.0, start_deg=10.0, stop_deg=10.0),
    ]
    destination = tmp_path / "calibration.json"
    calibration.export_calibration(entries, destination)

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["servos"][0]["channel"] == 0
    assert payload["servos"][1]["start_deg"] == pytest.approx(10.0)


def test_load_and_apply_calibration(tmp_path: Path) -> None:
    calibration_file = tmp_path / "calibration.json"
    calibration_file.write_text(
        json.dumps(
            {
                "servos": [
                    {"channel": 0, "min_deg": -30, "max_deg": 40, "start_deg": 10},
                    {"channel": 1, "min_deg": -20, "max_deg": 25},
                ]
            }
        ),
        encoding="utf-8",
    )

    calibration_data = calibration.load_calibration(calibration_file)
    driver = FakeDriver()
    session = calibration.ServoCalibrationSession(
        [0, 1],
        driver,
        default_min_deg=-90.0,
        default_max_deg=90.0,
        default_start_deg=0.0,
        initial_calibration=calibration_data,
    )

    assert driver.commands[0] == (0, pytest.approx(10.0))
    entry0 = session.entries[0]
    assert entry0.min_deg == pytest.approx(-30.0)
    assert entry0.max_deg == pytest.approx(40.0)
    assert entry0.start_set is True

    entry1 = session.entries[1]
    assert entry1.min_deg == pytest.approx(-20.0)
    assert entry1.max_deg == pytest.approx(25.0)
    assert entry1.start_deg == pytest.approx(0.0)
