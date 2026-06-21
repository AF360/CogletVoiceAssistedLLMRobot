import logging

import pytest

import startup_checks_impl
from startup_checks import StartupCheckError, check_openai_realtime_config


LOGGER = logging.getLogger("test")


def test_realtime_startup_check_requires_api_key(monkeypatch):
    monkeypatch.setattr(
        startup_checks_impl.importlib.util,
        "find_spec",
        lambda name: object(),
    )
    with pytest.raises(StartupCheckError, match="OPENAI_API_KEY"):
        check_openai_realtime_config({}, logger=LOGGER)


def test_realtime_startup_check_requires_websocket_dependency(monkeypatch):
    monkeypatch.setattr(
        startup_checks_impl.importlib.util,
        "find_spec",
        lambda name: None,
    )
    with pytest.raises(StartupCheckError, match="websocket-client"):
        check_openai_realtime_config(
            {"OPENAI_API_KEY": "test-api-key"},
            logger=LOGGER,
        )


def test_realtime_startup_check_rejects_invalid_vad(monkeypatch):
    monkeypatch.setattr(
        startup_checks_impl.importlib.util,
        "find_spec",
        lambda name: object(),
    )
    with pytest.raises(StartupCheckError, match="OPENAI_REALTIME_VAD_MODE"):
        check_openai_realtime_config(
            {
                "OPENAI_API_KEY": "test-api-key",
                "OPENAI_REALTIME_VAD_MODE": "invalid",
            },
            logger=LOGGER,
        )


def test_realtime_startup_check_valid_without_mode_selector(monkeypatch):
    monkeypatch.setattr(
        startup_checks_impl.importlib.util,
        "find_spec",
        lambda name: object(),
    )
    assert (
        check_openai_realtime_config(
            {
                "OPENAI_API_KEY": "test-api-key",
                "OPENAI_REALTIME_VAD_MODE": "semantic_vad",
            },
            logger=LOGGER,
        )
        is None
    )


def test_realtime_startup_check_ignores_unrelated_environment(monkeypatch):
    monkeypatch.setattr(
        startup_checks_impl.importlib.util,
        "find_spec",
        lambda name: object(),
    )
    assert (
        check_openai_realtime_config(
            {
                "OPENAI_API_KEY": "test-api-key",
                "OPENAI_REALTIME_VAD_MODE": "server_vad",
            },
            logger=LOGGER,
        )
        is None
    )
