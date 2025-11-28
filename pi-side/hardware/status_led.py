#!/usr/bin/env python3
import os
from enum import Enum
from rpi_ws281x import PixelStrip, Color


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
    """
    Control a single WS281x LED as a status indicator for Coglet.

    If ENABLE_LED in the environment is set to false/0/off, all calls are
    silently ignored.
    """

    def __init__(
        self,
        led_pin: int = 12,      # GPIO wired to the pixel shifter
        led_count: int = 1,
        brightness: int = 64,   # 0–255
        led_freq_hz: int = 800000,
        led_dma: int = 10,
        led_invert: bool = False,
        led_channel: int = 0,
        enabled: bool | None = None,  # explicitly overridable
    ) -> None:
        # Resolve ENV flag (default: True)
        if enabled is None:
            self._enabled = _env_bool("ENABLE_LED", default=True)
        else:
            self._enabled = enabled

        self._current_state: CogletState = CogletState.OFF

        if self._enabled:
            # Only initialize hardware when enabled
            self._strip = PixelStrip(
                led_count,
                led_pin,
                led_freq_hz,
                led_dma,
                led_invert,
                brightness,
                led_channel,
            )
            self._strip.begin()
            self.off()
        else:
            # No strip when disabled
            self._strip = None

    # --- Set low-level color ---

    def _set_rgb(self, r: int, g: int, b: int) -> None:
        """Directly set RGB (0–255) for LED 0."""
        if not self._enabled or self._strip is None:
            # LED is disabled -> do nothing
            return

        self._strip.setPixelColor(0, Color(r, g, b))
        self._strip.show()

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
        self._set_rgb(0, 0, 0)

    @property
    def current_state(self) -> CogletState:
        return self._current_state
