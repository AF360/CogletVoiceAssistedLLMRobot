"""Public startup-check API for Coglet's Local and Cloud launchers."""

from startup_checks_impl import (
    StartupCheckError,
    check_ollama_model,
    check_openai_realtime_config,
    check_piper_mqtt_connectivity,
    check_stt_health,
)

__all__ = [
    "StartupCheckError",
    "check_ollama_model",
    "check_openai_realtime_config",
    "check_piper_mqtt_connectivity",
    "check_stt_health",
]
