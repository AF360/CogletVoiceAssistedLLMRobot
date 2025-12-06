#!/usr/bin/env python3
import atexit
import os
from enum import Enum
from typing import Any

import board
import neopixel


def _env_bool(name: str, default: bool = True) -> bool:
    """
    Read a boolean value from the environment.
    TRUE, true, 1, yes, on -> True
    FALSE, false, 0, no, off -> False
    """
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class CogletState(str, Enum):
    AWAIT_WAKEWORD = "await_wakeword"   # yellow
    AWAIT_FOLLOWUP = "await_followup"   # violet
    LISTENING      = "listening"        # red
    THINKING       = "thinking"         # blue
    SPEAKING       = "speaking"         # green
    OFF            = "off"              # off


class StatusLED:
    """Drive a single NeoPixel (WS281x) as Coglet status indicator.

    If ENABLE_LED in the environment is set to false/0/off, the LED stays
    off and all calls become no-ops. The default pin is GPIO21 (board.D21),
    matching the verified reference script provided by the user.
    """

    def __init__(
        self,
        pixel_pin: Any = board.D21,
        num_pixels: int = 1,
        brightness: float | int = 0.3,
        pixel_order: Any = neopixel.RGB,
        auto_write: bool = False,
        enabled: bool | None = None,
    ) -> None:
        if enabled is None:
            self._enabled = _env_bool("ENABLE_LED", default=True)
        else:
            self._enabled = enabled

        self._current_state: CogletState = CogletState.OFF
        self._pixels: neopixel.NeoPixel | None = None

        if not self._enabled:
            return

        normalized_brightness = self._normalize_brightness(brightness)
        resolved_pin = self._resolve_pin(pixel_pin)

        try:
            self._pixels = neopixel.NeoPixel(
                resolved_pin,
                num_pixels,
                brightness=normalized_brightness,
                auto_write=auto_write,
                pixel_order=pixel_order,
            )
            self.off()
            atexit.register(self.off)
        except Exception:
            # Disable LED on any init error to avoid crashes during startup
            self._enabled = False
            self._pixels = None

    # --- Set low-level color ---

    def _set_rgb(self, r: int, g: int, b: int) -> None:
        """Directly set RGB (0–255) for LED 0."""
        if not self._enabled or self._pixels is None:
            return

        clamped = (
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))),
        )
        self._pixels.fill(clamped)
        if not self._pixels.auto_write:
            self._pixels.show()

    # --- High-level states ---

    def set_state(self, state: CogletState | str) -> None:
        """Set Coglet status and display the matching color."""
        if isinstance(state, str):
            try:
                state = CogletState(state.lower())
            except ValueError:
                state = CogletState.OFF

        self._current_state = state

        if state == CogletState.AWAIT_WAKEWORD:
            # Yellow
            self._set_rgb(255, 160, 0)
        elif state == CogletState.AWAIT_FOLLOWUP:
            # Violet
            self._set_rgb(180, 0, 255)
        elif state == CogletState.LISTENING:
            # Red
            self._set_rgb(255, 0, 0)
        elif state == CogletState.THINKING:
            # Blue
            self._set_rgb(0, 0, 255)
        elif state == CogletState.SPEAKING:
            # Green
            self._set_rgb(0, 255, 0)
        else:
            # OFF or unknown
            self.off()

    def off(self) -> None:
        self._current_state = CogletState.OFF
        self._set_rgb(0, 0, 0)

    @property
    def current_state(self) -> CogletState:
        return self._current_state

    # --- Helpers ---

    @staticmethod
    def _normalize_brightness(brightness: float | int) -> float:
        """Allow both 0–1.0 floats and legacy 0–255 integers for brightness."""
        if isinstance(brightness, int) and brightness > 1:
            return max(0.0, min(brightness, 255)) / 255.0
        return max(0.0, min(float(brightness), 1.0))

    @staticmethod
    def _resolve_pin(pixel_pin: Any) -> Any:
        """Allow board pin objects or fallback int GPIO numbers."""
        if isinstance(pixel_pin, int):
            board_attr = f"D{pixel_pin}"
            if hasattr(board, board_attr):
                return getattr(board, board_attr)
        return pixel_pin
