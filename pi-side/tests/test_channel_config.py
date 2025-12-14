import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware.channel_config import parse_channel_list, resolve_channel_list


def test_parse_channel_list_filters_invalid_entries(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("channel-config-test")
    caplog.set_level(logging.WARNING)
    result = parse_channel_list("0, 5, nope, 16, 3", logger=logger)
    assert result == [0, 5, 3]
    assert "nope" in caplog.text
    assert "16" in caplog.text


def test_resolve_channel_list_defaults_when_env_missing() -> None:
    logger = logging.getLogger("channel-config-test")
    default = [1, 2]
    result = resolve_channel_list(
        env_value=None,
        default=default,
        allow_empty=False,
        logger=logger,
        env_name="TEST_CHANNELS",
    )
    assert result == default


def test_resolve_channel_list_can_disable_channels(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("channel-config-test")
    caplog.set_level(logging.INFO)
    result = resolve_channel_list(
        env_value="  ",
        default=[8, 9],
        allow_empty=True,
        logger=logger,
        env_name="TEST_CHANNELS",
    )
    assert result == []
    assert "disabled" in caplog.text


def test_resolve_channel_list_falls_back_on_invalid_data(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("channel-config-test")
    caplog.set_level(logging.WARNING)
    result = resolve_channel_list(
        env_value="foo",
        default=[8, 9],
        allow_empty=True,
        logger=logger,
        env_name="TEST_CHANNELS",
    )
    assert result == [8, 9]
    assert "falling back" in caplog.text
