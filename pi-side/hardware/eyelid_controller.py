from __future__ import annotations

import random
import threading
import time
from typing import Callable

from hardware.pca9685_servo import Servo

__all__ = ["EyelidController"]


class EyelidController:
    """Threaded controller to drive an eyelid servo with autonomous blinking.

    The controller interpolates between an "open" and a "closed" servo angle,
    runs a background blink loop in ``auto`` mode and exposes a small public API
    for the animation layer.
    """

    def __init__(
        self,
        servo: Servo,
        *,
        open_angle_deg: float,
        closed_angle_deg: float,
        sleep_fraction: float = 0.7,
        blink_interval_min_s: float = 3.0,
        blink_interval_max_s: float = 7.0,
        blink_close_s: float = 0.06,
        blink_hold_s: float = 0.04,
        blink_open_s: float = 0.07,
        rng: random.Random | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if blink_interval_min_s < 0.0 or blink_interval_max_s < blink_interval_min_s:
            raise ValueError("Blink interval range must be non-negative and ordered")
        if not 0.0 <= sleep_fraction <= 1.0:
            raise ValueError("sleep_fraction must be within [0.0, 1.0]")

        self._servo = servo
        self._open_angle = open_angle_deg
        self._closed_angle = closed_angle_deg
        self._sleep_fraction = sleep_fraction
        self._blink_interval_min_s = blink_interval_min_s
        self._blink_interval_max_s = blink_interval_max_s
        self._blink_close_s = blink_close_s
        self._blink_hold_s = blink_hold_s
        self._blink_open_s = blink_open_s
        self._rng = rng or random.Random()
        self._monotonic = monotonic

        self._lock = threading.RLock()
        self._mode: str = "auto"
        self._blink_in_progress = False
        self._stop_event = threading.Event()
        self._target_override: float | None = None
        self._override_until = 0.0

        self._blink_thread = threading.Thread(
            target=self._blink_loop,
            name="EyelidBlinkThread",
            daemon=True,
        )
        self._blink_thread.start()

        self.set_open()

    # ---------------------------- Public API ----------------------------
    def set_mode(self, mode: str) -> None:
        """Switch between modes: auto, hold, closed, sleep."""

        if mode not in {"auto", "hold", "closed", "sleep"}:
            raise ValueError(f"Unsupported eyelid mode: {mode}")
        with self._lock:
            self._mode = mode
            if mode == "closed":
                self._apply_fraction(1.0)
            elif mode == "sleep":
                self._apply_fraction(self._sleep_fraction)
            elif mode == "auto":
                self._apply_fraction(0.0)
            # hold: keep current position

    def set_override(self, target_angle: float, duration_s: float = 1.0) -> None:
        """Temporarily override eyelid angle, suspending blinking."""

        with self._lock:
            self._mode = "override"
            self._target_override = target_angle
            self._override_until = self._monotonic() + duration_s
            self._servo.move_to(self._target_override)
            self._sync_servo()

    def angle_for_fraction(self, fraction: float) -> float:
        """Return the absolute angle for the given closed-state fraction."""

        with self._lock:
            return self._fraction_to_angle(fraction)

    def blink_once(self) -> None:
        """Trigger a single blink in the background (non-blocking)."""

        thread = threading.Thread(target=self._do_blink, name="EyelidBlink", daemon=True)
        thread.start()

    def set_open(self) -> None:
        """Explicitly open the eyelid (disables blink suppression)."""

        self._apply_fraction(0.0)

    def shutdown(self, *, join_timeout: float = 1.0) -> None:
        """Stop the background loop to avoid leaking threads in tests."""

        self._stop_event.set()
        self._blink_thread.join(timeout=join_timeout)

    # --------------------------- Internals ---------------------------
    def _fraction_to_angle(self, fraction: float) -> float:
        fraction = max(0.0, min(1.0, fraction))
        return self._open_angle + fraction * (self._closed_angle - self._open_angle)

    def _apply_fraction(self, fraction: float) -> None:
        target = self._fraction_to_angle(fraction)
        self._servo.move_to(target)
        self._sync_servo()

    def _sync_servo(self, *, steps: int = 3, dt: float = 0.05) -> None:
        for _ in range(steps):
            self._servo.update(dt)

    def _do_blink(self) -> None:
        with self._lock:
            if self._mode not in {"auto", "hold"}:
                return
            if self._blink_in_progress:
                return
            self._blink_in_progress = True

        try:
            self._animate(from_fraction=0.0, to_fraction=1.0, duration=self._blink_close_s)
            self._sleep(self._blink_hold_s)
            self._animate(from_fraction=1.0, to_fraction=0.0, duration=self._blink_open_s)
        finally:
            with self._lock:
                self._blink_in_progress = False

    def _animate(self, *, from_fraction: float, to_fraction: float, duration: float, steps: int = 8) -> None:
        if duration <= 0.0:
            self._apply_fraction(to_fraction)
            return
        step_duration = duration / max(1, steps)
        for idx in range(steps + 1):
            with self._lock:
                if self._mode not in {"auto", "hold"}:
                    break
                fraction = from_fraction + (to_fraction - from_fraction) * (idx / steps)
                self._apply_fraction(fraction)
            self._sleep(step_duration)

    def _blink_loop(self) -> None:
        while not self._stop_event.is_set():
            wait_s = self._rng.uniform(self._blink_interval_min_s, self._blink_interval_max_s)
            if self._stop_event.wait(wait_s):
                break
            with self._lock:
                if self._mode == "override":
                    if self._monotonic() < self._override_until:
                        if self._target_override is not None:
                            self._servo.move_to(self._target_override)
                            self._sync_servo()
                        continue
                    self._mode = "auto"
                if self._mode != "auto" or self._blink_in_progress:
                    continue
            self._do_blink()

    def _sleep(self, duration: float) -> None:
        target = self._monotonic() + max(0.0, duration)
        while not self._stop_event.is_set():
            remaining = target - self._monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.01))
