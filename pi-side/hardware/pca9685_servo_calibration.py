"""Interactive CLI for calibrating PCA9685 servo channels."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Protocol, Sequence


class AngleDriver(Protocol):
    """Minimal driver interface for setting a servo angle."""

    def set_angle(self, channel: int, angle_deg: float) -> None:
        """Set ``angle_deg`` (degrees) for the given channel."""

    def close(self) -> None:  # pragma: no cover - optional resource cleanup
        """Close driver resources if needed."""


@dataclass
class ServoCalibrationEntry:
    """Represents calibration data for a servo."""

    channel: int
    min_deg: float
    max_deg: float
    start_deg: float
    stop_deg: float
    min_set: bool = False
    max_set: bool = False
    start_set: bool = False
    stop_set: bool = False

    def clamp(self, angle: float) -> float:
        return clamp(angle, self.min_deg, self.max_deg)

    def clamp_stop(self, angle: float) -> float:
        return clamp(angle, self.min_deg, self.max_deg)


class ServoCalibrationSession:
    """Manage calibration for multiple servos."""

    def __init__(
        self,
        channels: Sequence[int],
        driver: AngleDriver,
        *,
        step_deg: float = 1.0,
        default_min_deg: float = -90.0,
        default_max_deg: float = 90.0,
        default_start_deg: float = 0.0,
        default_stop_deg: float | None = None,
        channel_labels: Mapping[int, str] | None = None,
        initial_calibration: Mapping[int, Mapping[str, float]] | None = None,
    ) -> None:
        if not channels:
            raise ValueError("At least one channel needs to be calibrated")
        if step_deg <= 0:
            raise ValueError("step_deg must be positive")
        if default_min_deg >= default_max_deg:
            raise ValueError("default_min_deg must be smaller than the default_max_deg value")
        self.step_deg = clamp(step_deg, 1.0, 10.0)
        self._driver = driver
        self._default_min_deg = default_min_deg
        self._default_max_deg = default_max_deg
        self._default_start_deg = clamp(default_start_deg, default_min_deg, default_max_deg)
        stop_value = self._default_start_deg if default_stop_deg is None else default_stop_deg
        self._default_stop_deg = clamp(stop_value, default_min_deg, default_max_deg)
        self._channel_labels = dict(channel_labels or {})
        self.entries = [
            ServoCalibrationEntry(
                channel=channel,
                min_deg=default_min_deg,
                max_deg=default_max_deg,
                start_deg=self._default_start_deg,
                stop_deg=self._default_stop_deg,
            )
            for channel in channels
        ]
        self._apply_initial_calibration(initial_calibration or {})
        self._factory_values = {
            entry.channel: (
                entry.min_deg,
                entry.max_deg,
                entry.start_deg,
                entry.stop_deg,
                entry.min_set,
                entry.max_set,
                entry.start_set,
                entry.stop_set,
            )
            for entry in self.entries
        }
        self._current_index = 0
        self._current_angle = self.entries[0].start_deg
        self._last_applied_angle: float | None = None
        self._apply_angle()

    @property
    def current_entry(self) -> ServoCalibrationEntry:
        return self.entries[self._current_index]

    @property
    def current_angle(self) -> float:
        return self._current_angle

    def process_command(self, command: str) -> bool:
        """Process a single key press."""

        if not command:
            return True
        if command == "Q":
            return False

        entry = self.current_entry
        if command == "<":
            self._set_current_angle(entry.min_deg)
        elif command == ">":
            self._set_current_angle(entry.max_deg)
        elif command == "c":
            self._set_current_angle(entry.start_deg)
        elif command == "u":
            self._set_current_angle(entry.clamp(self._current_angle + self.step_deg))
        elif command == "d":
            self._set_current_angle(entry.clamp(self._current_angle - self.step_deg))
        elif command == "U":
            entry.max_deg = self._current_angle
            entry.max_set = True
            if entry.min_deg > entry.max_deg:
                entry.min_deg = entry.max_deg
            entry.start_deg = entry.clamp(entry.start_deg)
            entry.stop_deg = entry.clamp_stop(entry.stop_deg)
        elif command == "D":
            entry.min_deg = self._current_angle
            entry.min_set = True
            if entry.max_deg < entry.min_deg:
                entry.max_deg = entry.min_deg
            entry.start_deg = entry.clamp(entry.start_deg)
            entry.stop_deg = entry.clamp_stop(entry.stop_deg)
        elif command == "A":
            entry.start_deg = entry.clamp(self._current_angle)
            entry.start_set = True
        elif command == "Z":
            entry.stop_deg = entry.clamp_stop(self._current_angle)
            entry.stop_set = True
        elif command == "x":
            self._reset_current_entry()
        elif command == "n":
            return self._advance_to_next_servo()
        elif command == "p":
            return self._move_to_previous_servo()
        elif command == "+":
            self.step_deg = min(10.0, self.step_deg + 1.0)
        elif command == "-":
            self.step_deg = max(1.0, min(10.0, self.step_deg - 1.0))
        else:
            return True

        return True

    def _advance_to_next_servo(self) -> bool:
        self._current_index = (self._current_index + 1) % len(self.entries)
        self._current_angle = self.current_entry.start_deg
        self._apply_angle(force=True)
        return True

    def _move_to_previous_servo(self) -> bool:
        self._current_index = (self._current_index - 1) % len(self.entries)
        self._current_angle = self.current_entry.start_deg
        self._apply_angle(force=True)
        return True

    def _reset_current_entry(self) -> None:
        entry = self.current_entry
        defaults = self._factory_values.get(
            entry.channel,
            (
                self._default_min_deg,
                self._default_max_deg,
                self._default_start_deg,
                self._default_stop_deg,
                False,
                False,
                False,
                False,
            ),
        )
        (
            entry.min_deg,
            entry.max_deg,
            entry.start_deg,
            entry.stop_deg,
            entry.min_set,
            entry.max_set,
            entry.start_set,
            entry.stop_set,
        ) = defaults
        self._set_current_angle(entry.start_deg)

    def _apply_initial_calibration(self, calibration: Mapping[int, Mapping[str, float]]) -> None:
        if not calibration:
            return
        for entry in self.entries:
            channel_data = calibration.get(entry.channel)
            if not channel_data:
                continue
            min_deg = channel_data.get("min_deg", entry.min_deg)
            max_deg = channel_data.get("max_deg", entry.max_deg)
            if min_deg >= max_deg:
                min_deg = entry.min_deg
                max_deg = entry.max_deg
            entry.min_deg = min_deg
            entry.max_deg = max_deg
            entry.min_set = "min_deg" in channel_data
            entry.max_set = "max_deg" in channel_data
            if "start_deg" in channel_data:
                entry.start_deg = clamp(channel_data["start_deg"], entry.min_deg, entry.max_deg)
                entry.start_set = True
            else:
                entry.start_deg = clamp(entry.start_deg, entry.min_deg, entry.max_deg)
            if "stop_deg" in channel_data:
                entry.stop_deg = clamp(channel_data["stop_deg"], entry.min_deg, entry.max_deg)
                entry.stop_set = True
            else:
                entry.stop_deg = clamp(entry.stop_deg, entry.min_deg, entry.max_deg)

    def _set_current_angle(self, angle: float) -> None:
        if angle == self._current_angle:
            return
        self._current_angle = angle
        self._apply_angle()

    def _apply_angle(self, *, force: bool = False) -> None:
        if not force and self._last_applied_angle == self._current_angle:
            return
        entry = self.current_entry
        self._driver.set_angle(entry.channel, self._current_angle)
        self._last_applied_angle = self._current_angle

    def results(self) -> List[ServoCalibrationEntry]:
        return list(self.entries)

    def channel_label(self, channel: int) -> str | None:
        return self._channel_labels.get(channel)


class PCA9685AngleDriver:
    """Write angle values to a PCA9685 channel."""

    def __init__(
        self,
        *,
        i2c_address: int = 0x40,
        pwm_frequency_hz: float = 50.0,
        min_pulse_us: float = 500.0,
        max_pulse_us: float = 2500.0,
        min_angle_deg: float = -90.0,
        max_angle_deg: float = 90.0,
    ) -> None:
        if pwm_frequency_hz <= 0:
            raise ValueError("pwm_frequency_hz must be positive")
        if min_pulse_us <= 0 or max_pulse_us <= 0:
            raise ValueError("Pulsewidth must be positive")
        if min_pulse_us >= max_pulse_us:
            raise ValueError("min_pulse_us must be smaller than max_pulse_us")
        if min_angle_deg >= max_angle_deg:
            raise ValueError("min_angle_deg must be smaller than max_angle_deg")
        self._pwm_frequency_hz = pwm_frequency_hz
        self._min_pulse_us = min_pulse_us
        self._max_pulse_us = max_pulse_us
        self._period_us = 1_000_000.0 / pwm_frequency_hz
        self._min_angle_deg = min_angle_deg
        self._max_angle_deg = max_angle_deg
        self._angle_span = max_angle_deg - min_angle_deg
        self._pca = self._create_controller(i2c_address, pwm_frequency_hz)

    def _create_controller(self, address: int, freq: float):  
        try:
            import importlib
            import board
            import busio
            from adafruit_pca9685 import PCA9685
            importlib.import_module("digitalio")  
        except (ImportError, AttributeError, NameError) as exc:  
            raise RuntimeError(
                "The CircuitPython-drivers (adafruit-blinka incl. board/busio/digitalio and "
                "adafruit-circuitpython-pca9685) are missing or not compatiblem. Please install them with: "
                "pip install -r pi-side/requirements.txt  (Python 3.11, activiate I²C in raspi-config)."
            ) from exc
        i2c = busio.I2C(board.SCL, board.SDA)
        controller = PCA9685(i2c, address=address)
        controller.frequency = int(freq)
        return controller

    def set_angle(self, channel: int, angle_deg: float) -> None:
        angle_deg = clamp(angle_deg, self._min_angle_deg, self._max_angle_deg)
        normalized = (angle_deg - self._min_angle_deg) / self._angle_span
        pulse_span = self._max_pulse_us - self._min_pulse_us
        pulse = self._min_pulse_us + clamp(normalized, 0.0, 1.0) * pulse_span
        duty_cycle = int(round(clamp(pulse / self._period_us, 0.0, 1.0) * 0xFFFF))
        self._pca.channels[channel].duty_cycle = duty_cycle

    def close(self) -> None:  # pragma: no cover - hardware access
        if hasattr(self._pca, "deinit"):
            self._pca.deinit()

    def __enter__(self) -> "PCA9685AngleDriver":  # pragma: no cover - context manager
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # pragma: no cover
        self.close()


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def parse_channels(expr: str) -> List[int]:
    channels: List[int] = []
    for part in expr.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start_str, end_str = part.split('-', 1)
            start = int(start_str, 0)
            end = int(end_str, 0)
            step = 1 if end >= start else -1
            channels.extend(range(start, end + step, step))
        else:
            channels.append(int(part, 0))
    unique = []
    for channel in channels:
        if channel not in unique:
            unique.append(channel)
    return unique


def export_calibration(entries: Iterable[ServoCalibrationEntry], destination: Path) -> None:
    data = {
        "servos": [
            {
                "channel": entry.channel,
                "min_deg": entry.min_deg,
                "max_deg": entry.max_deg,
                "start_deg": entry.start_deg,
                "stop_deg": entry.stop_deg,
            }
            for entry in entries
        ]
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_calibration(source: Path) -> Mapping[int, Mapping[str, float]]:
    if not source.exists():
        return {}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warnung: Calibration from {source} could not be read: {exc}", file=sys.stderr)
        return {}
    servos = payload.get("servos")
    if not isinstance(servos, list):
        return {}
    parsed: dict[int, dict[str, float]] = {}
    for entry in servos:
        if not isinstance(entry, dict) or "channel" not in entry:
            continue
        try:
            channel = int(entry["channel"])
        except (TypeError, ValueError):
            continue
        channel_data: dict[str, float] = {}
        for key in ("min_deg", "max_deg", "start_deg", "stop_deg"):
            if key not in entry:
                continue
            try:
                channel_data[key] = float(entry[key])
            except (TypeError, ValueError):
                continue
        if channel_data:
            parsed[channel] = channel_data
    return parsed


class KeyReader:
    """Read individual key presses from the terminal."""

    def __init__(self) -> None:
        self._stdin = sys.stdin

    def read_key(self) -> str:
        if not self._stdin.isatty():
            value = input("Enter command and press Enter: ")
            return value[:1] if value else ""
        try:
            import termios
            import tty
        except ImportError:
            value = input("Enter command and press Enter: ")
            return value[:1] if value else ""
        fd = self._stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            char = self._stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        if char == "\x03":  # Ctrl+C
            raise KeyboardInterrupt
        return char


def format_value(value: float, is_set: bool) -> str:
    """Format stored calibration values with emphasis."""

    if not is_set:
        return "-"
    return f"\033[33m{value:.1f}°\033[0m"


def run_interactive(session: ServoCalibrationSession) -> None:
    key_reader = KeyReader()
    header = (
        "\033[1mSteuerung:\033[0m "
        "\033[33m<\033[0m (zu Min-Winkel), \033[33m>\033[0m (zu Max-Winkel), "
        "\033[33mc\033[0m (Hardware-Center), \033[33m+/-\033[0m (Schritt 1–10°), "
        "\033[33mu\033[0m (+ Schritt), \033[33mU\033[0m (Max speichern), "
        "\033[33md\033[0m (- Schritt), \033[33mD\033[0m (Min speichern), "
        "\033[33mA\033[0m (Start speichern), \033[33mZ\033[0m (Stop speichern), "
        "\033[33mx\033[0m (Reset Kanal), \033[33mn/p\033[0m (Nächster/Vorheriger), "
        "\033[33mQ\033[0m (Speichern & Beenden)"
    )
    print(header)

    last_channel: int | None = None
    status_line_active = False

    while True:
        entry = session.current_entry

        if entry.channel != last_channel:
            if status_line_active:
                print()
                status_line_active = False
            channel_label = session.channel_label(entry.channel)
            channel_display = f"Kanal {entry.channel}"
            if channel_label:
                channel_display += f" ({channel_label})"
            print(f"\n\033[1m=== Servo-{channel_display} ===\033[0m")
            last_channel = entry.channel

        min_display = format_value(entry.min_deg, entry.min_set)
        max_display = format_value(entry.max_deg, entry.max_set)
        start_display = format_value(entry.start_deg, entry.start_set)
        stop_display = format_value(entry.stop_deg, entry.stop_set)
        step_display = f"{session.step_deg:.1f}°"
        status = (
            f"\033[36mCurrent\033[0m: {session.current_angle:.1f}° | "
            f"\033[36mStep\033[0m: {step_display} | "
            f"\033[36mMin\033[0m: {min_display} | "
            f"\033[36mMax\033[0m: {max_display} | "
            f"\033[36mStart\033[0m: {start_display} | "
            f"\033[36mStop\033[0m: {stop_display}"
        )

        if not status_line_active:
            sys.stdout.write(status)
            sys.stdout.flush()
            status_line_active = True
        else:
            sys.stdout.write("\r\033[K" + status)
            sys.stdout.flush()

        command = key_reader.read_key()
        cont = session.process_command(command)
        if not cont:
            break

    if status_line_active:
        print()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibrates PCA9685-servos interactively")
    parser.add_argument(
        "--channels",
        default="0-9",
        help="Commaseperated list or interval (0-9) of the channels to calibrate",
    )
    parser.add_argument("--step", type=float, default=1.0, help="Angle change per keypress in degree")
    parser.add_argument("--min-angle", type=float, default=-90.0, help="Initial minimum angle")
    parser.add_argument("--max-angle", type=float, default=90.0, help="Initial maximum angle")
    parser.add_argument("--start-angle", type=float, default=0.0, help="Initial start angle")
    parser.add_argument("--i2c-address", type=lambda v: int(v, 0), default="0x40", help="I2C-adress of the PCA9685 board")
    parser.add_argument("--frequency", type=float, default=50.0, help="PWM-frequency in Hz")
    parser.add_argument("--min-pulse", type=float, default=500.0, help="Minimum pulse width in micro-seconds")
    parser.add_argument("--max-pulse", type=float, default=2500.0, help="Maximum pulse width in micro-seconds ")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("servo-calibration.json"),  # <-- HIER GEÄNDERT auf Bindestrich!
        help="File with servo calibration data",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    channels = parse_channels(args.channels)
    if not channels:
        raise SystemExit("No valid channels stated")
    calibration_data = load_calibration(args.output)
    if calibration_data:
        print(f"Loading existing calibration from {args.output}...")
    with PCA9685AngleDriver(
        i2c_address=args.i2c_address,
        pwm_frequency_hz=args.frequency,
        min_pulse_us=args.min_pulse,
        max_pulse_us=args.max_pulse,
        min_angle_deg=args.min_angle,
        max_angle_deg=args.max_angle,
    ) as driver:
        session = ServoCalibrationSession(
            channels,
            driver,
            step_deg=args.step,
            default_min_deg=args.min_angle,
            default_max_deg=args.max_angle,
            default_start_deg=args.start_angle,
            initial_calibration=calibration_data,
        )
        try:
            run_interactive(session)
        except KeyboardInterrupt:
            print("\nCalibration cancelled, previous values are stored...")
    results = session.results()
    export_calibration(results, args.output)
    print(f"\nCalibration saved in {args.output}")
    print("Values:")
    for entry in results:
        print(
            f"Channel {entry.channel}: Min {entry.min_deg:.1f}° | Max {entry.max_deg:.1f}° | Start {entry.start_deg:.1f}° | Stop {entry.stop_deg:.1f}°"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI-Einstiegspunkt
    raise SystemExit(main())
    
