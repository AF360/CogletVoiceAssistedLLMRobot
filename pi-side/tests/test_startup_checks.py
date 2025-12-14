import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from startup_checks import (
    StartupCheckError,
    check_ollama_model,
    check_piper_mqtt_connectivity,
    check_stt_health,
)


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_check_stt_health_ok(monkeypatch):
    logger = logging.getLogger("test")

    def fake_get(url, timeout):
        return _Response({"ok": True, "model": "m", "device": "cpu"})

    monkeypatch.setattr("startup_checks.requests", SimpleNamespace(get=fake_get))

    check_stt_health("http://example", timeout=0.1, logger=logger)


def test_check_stt_health_unhealthy(monkeypatch):
    logger = logging.getLogger("test")

    def fake_get(url, timeout):
        return _Response({"ok": False})

    monkeypatch.setattr("startup_checks.requests", SimpleNamespace(get=fake_get))

    with pytest.raises(StartupCheckError):
        check_stt_health("http://example", timeout=0.1, logger=logger)


def test_check_ollama_model_missing(monkeypatch):
    logger = logging.getLogger("test")

    def fake_get(url, timeout):
        return _Response({"models": [{"name": "other"}]})

    monkeypatch.setattr("startup_checks.requests", SimpleNamespace(get=fake_get))

    with pytest.raises(StartupCheckError):
        check_ollama_model("http://ollama", "wanted", timeout=0.1, logger=logger)


def test_check_ollama_model_ok(monkeypatch):
    logger = logging.getLogger("test")

    def fake_get(url, timeout):
        return _Response({"models": [{"name": "wanted"}]})

    monkeypatch.setattr("startup_checks.requests", SimpleNamespace(get=fake_get))

    check_ollama_model("http://ollama", "wanted", timeout=0.1, logger=logger)


def test_check_piper_mqtt_connectivity(monkeypatch):
    logger = logging.getLogger("test")

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.disconnected = False

        def username_pw_set(self, username=None, password=None):
            self.username = username
            self.password = password

        def tls_set(self):
            self.tls_enabled = True

        def connect(self, host, port, keepalive=30):
            self.conn = SimpleNamespace(host=host, port=port, keepalive=keepalive)
            return 0

        def disconnect(self):
            self.disconnected = True

    check_piper_mqtt_connectivity(
        host="localhost",
        port=1883,
        username="user",
        password="pass",
        use_tls=True,
        protocol=5,
        clean_start_supported=True,
        clean_session_supported=True,
        clean_start_flag=1,
        client_factory=FakeClient,
        logger=logger,
    )


def test_check_piper_mqtt_connectivity_rc_error():
    logger = logging.getLogger("test")

    class FailingClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def connect(self, host, port, keepalive=30):
            return 2

        def disconnect(self):
            self.disconnected = True

    with pytest.raises(StartupCheckError):
        check_piper_mqtt_connectivity(
            host="localhost",
            port=1883,
            username="",
            password="",
            use_tls=False,
            protocol=5,
            clean_start_supported=False,
            clean_session_supported=False,
            clean_start_flag=1,
            client_factory=FailingClient,
            logger=logger,
        )
