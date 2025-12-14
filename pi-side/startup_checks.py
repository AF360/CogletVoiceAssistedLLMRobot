"""Startup dependency checks for Coglet Pi.

This module validates the availability of STT (Whisper HTTP), LLM (Ollama),
and TTS (Piper via MQTT) services before the main loop starts.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable, Optional

try:
    import requests
    _REQUESTS_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency guard
    requests = None  # type: ignore
    _REQUESTS_IMPORT_ERROR = exc

try:
    import paho.mqtt.client as mqtt
except Exception:  # pragma: no cover - optional at runtime
    mqtt = None  # type: ignore


class StartupCheckError(RuntimeError):
    """Raised when a startup dependency is not available."""


def _require_requests() -> None:
    if requests is None:
        raise StartupCheckError("requests library is not installed") from _REQUESTS_IMPORT_ERROR


def check_stt_health(stt_url: str, *, timeout: float = 3.0, logger: logging.Logger) -> None:
    """Ensure the Whisper STT HTTP endpoint responds with ok=true."""

    _require_requests()
    health_url = f"{stt_url.rstrip('/')}/healthz"
    try:
        response = requests.get(health_url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # pragma: no cover - network errors are covered below
        raise StartupCheckError(f"STT service unreachable at {health_url}: {exc}") from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise StartupCheckError(f"STT service at {health_url} returned an unhealthy status: {payload!r}")

    logger.info(
        "STT reachable at %s (model=%s, device=%s)",
        health_url,
        payload.get("model", "unknown"),
        payload.get("device", "unknown"),
    )


def check_ollama_model(
    ollama_url: str,
    model: str,
    *,
    timeout: float = 3.0,
    logger: logging.Logger,
) -> None:
    """Validate that the Ollama server is reachable and the configured model exists."""

    _require_requests()
    tags_url = f"{ollama_url.rstrip('/')}/api/tags"
    try:
        response = requests.get(tags_url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # pragma: no cover - network errors are covered below
        raise StartupCheckError(f"Ollama service unreachable at {tags_url}: {exc}") from exc

    models = []
    try:
        models = [item.get("name", "") for item in payload.get("models", []) if isinstance(item, dict)]
    except Exception:
        models = []

    if model and model not in models:
        raise StartupCheckError(
            "Ollama model '%s' is not available (found: %s)" % (model, ", ".join(m for m in models if m) or "none")
        )

    logger.info("Ollama reachable at %s (model=%s)", tags_url, model or "<unspecified>")


def check_piper_mqtt_connectivity(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    protocol: int,
    clean_start_supported: bool,
    clean_session_supported: bool,
    clean_start_flag: int,
    client_factory: Optional[Callable[..., mqtt.Client]] = None,
    logger: logging.Logger,
) -> None:
    """Attempt a short-lived MQTT connection to the Piper broker."""

    if not host:
        raise StartupCheckError("Piper MQTT host is not configured")
    if mqtt is None and client_factory is None:
        raise StartupCheckError("paho-mqtt is not installed")

    protocol_is_v5 = False
    if mqtt is not None:
        protocol_is_v5 = protocol == getattr(mqtt, "MQTTv5", None)

    ctor: Callable[..., mqtt.Client] = client_factory or mqtt.Client
    client_kwargs = {
        "client_id": f"coglet-pi-check-{uuid.uuid4().hex[:8]}",
        "protocol": protocol,
    }

    if protocol_is_v5:
        if clean_start_supported:
            client_kwargs["clean_start"] = clean_start_flag
    else:
        if clean_session_supported:
            client_kwargs["clean_session"] = True
        elif clean_start_supported:
            client_kwargs["clean_start"] = clean_start_flag

    client = ctor(**client_kwargs)

    if username or password:
        client.username_pw_set(username or None, password or None)
    if use_tls:
        client.tls_set()

    try:
        rc = client.connect(host, port, keepalive=30)
    except Exception as exc:  # pragma: no cover - network stack errors are covered below
        raise StartupCheckError(f"Piper MQTT connection to {host}:{port} failed: {exc}") from exc
    finally:
        try:
            client.disconnect()
        except Exception:
            pass

    if rc != 0:
        raise StartupCheckError(f"Piper MQTT connection to {host}:{port} returned rc={rc}")

    logger.info("Piper MQTT reachable at %s:%s", host, port)
