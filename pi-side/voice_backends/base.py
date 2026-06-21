"""Small backend boundary shared by Local and OpenAI Realtime modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class BackendResult:
    """Result returned after a backend handled one wake-word session."""

    handled: bool
    exit_requested: bool = False
    fallback_to_local: bool = False
    reason: str = ""


class VoiceBackend(Protocol):
    """Minimal protocol for backend implementations used by coglet-local.py."""

    name: str

    def handle_wake_session(self, context: "BackendContext") -> BackendResult:
        """Handle a full post-wake conversation session."""


@dataclass
class BackendContext:
    """Runtime callbacks and objects owned by coglet-local.py.

    Keeping these as callbacks avoids importing robot hardware modules from the
    backend package and keeps mode-specific logic out of servo, LED and camera
    code.
    """

    recorder: Any
    wakeword: Any
    shutdown_event: Any
    logger: Any
    say: Callable[..., Any]
    set_led_state: Callable[[Any], None]
    states: Any
    anim_listen_start: Callable[[], None]
    anim_listen_stop: Callable[[], None]
    anim_think_start: Callable[[], None]
    anim_think_stop: Callable[[], None]
    anim_talk_start: Callable[[], None]
    anim_talk_stop: Callable[[], None]
    is_program_exit_command: Callable[[str], bool]
    is_email_request: Callable[[str], bool]
    handle_email_request: Callable[[str, Any, Any], bool]
    normalize_command_text: Callable[[str], str]
    eoc_ack: str
    model_byebye: str
