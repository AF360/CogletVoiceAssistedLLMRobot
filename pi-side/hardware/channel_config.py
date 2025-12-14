"""Helpers for parsing PCA9685 channel assignments from env variables."""

from __future__ import annotations

import logging
from typing import List, Sequence

__all__ = ["parse_channel_list", "resolve_channel_list"]


def parse_channel_list(text: str, *, logger: logging.Logger) -> List[int]:
    """Return all valid PCA9685 channel indices contained in *text*."""

    result: List[int] = []
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            continue
        try:
            idx = int(token)
        except ValueError:
            logger.warning("Ignoring invalid PCA9685 channel %r", token)
            continue
        if not 0 <= idx <= 15:
            logger.warning("Ignoring out-of-range PCA9685 channel %d", idx)
            continue
        result.append(idx)
    return result


def resolve_channel_list(
    *,
    env_value: str | None,
    default: Sequence[int],
    allow_empty: bool,
    logger: logging.Logger,
    env_name: str,
) -> List[int]:
    """Pick the channel list to use, respecting optional env overrides."""

    default_list = list(default)
    if env_value is None:
        return default_list

    parsed = parse_channel_list(env_value, logger=logger)
    if parsed:
        return parsed

    if env_value.strip():
        logger.warning(
            "No valid PCA9685 channels in %s=%r → falling back to defaults %s",
            env_name,
            env_value,
            default_list,
        )
        return default_list

    if allow_empty:
        logger.info("%s disabled (no channels configured)", env_name)
        return []
    return default_list
