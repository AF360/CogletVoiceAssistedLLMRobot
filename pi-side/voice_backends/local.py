"""Adapter for the existing Local Mode implementation.

The adapter intentionally delegates back to coglet-local.py callbacks instead of
reimplementing the Aurora pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from .base import BackendContext, BackendResult


@dataclass
class LocalBackend:
    """Thin wrapper around the proven local turn handler."""

    handle_turn: Callable[[BackendContext], BackendResult]
    name: str = "local"

    def handle_wake_session(self, context: BackendContext) -> BackendResult:
        return self.handle_turn(context)


def result_from_exit(exit_requested: bool = False) -> BackendResult:
    return BackendResult(handled=True, exit_requested=exit_requested)
