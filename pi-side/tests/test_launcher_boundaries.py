from __future__ import annotations

import ast
from pathlib import Path


PI_SIDE = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (PI_SIDE / name).read_text(encoding="utf-8")


def test_cloud_launcher_is_independent_from_local_mode() -> None:
    source = _source("coglet-cloud.py")
    assert "coglet-local.py" not in source
    assert "_load_coglet_module" not in source
    assert "importlib.util" not in source
    assert "OpenAIRealtimeConfig.from_env()" in source
    ast.parse(source)


def test_local_launcher_is_thin_and_explicit() -> None:
    source = _source("coglet-local.py")
    assert "import local_mode" in source
    assert "from robot_runtime import get_anim_servo" in source
    assert "local_mode._get_anim_servo = get_anim_servo" in source
    ast.parse(source)


def test_local_implementation_uses_shared_runtime() -> None:
    source = _source("local_mode.py")
    assert "from robot_runtime import" in source
    assert "class ServoInitBundle" not in source
    assert "def _initialize_all_servos" not in source
    assert "OpenAIRealtimeBackend" not in source
    ast.parse(source)


def test_shared_modules_parse() -> None:
    for name in (
        "command_utils.py",
        "robot_runtime.py",
        "hardware/robot_runtime.py",
    ):
        ast.parse(_source(name))


def test_one_shared_environment_template_without_backend_selector() -> None:
    env_source = _source("env-exports.sh.example")
    readme = _source("README.md")

    assert not (PI_SIDE / "env-openai-realtime.example.sh").exists()
    assert not (PI_SIDE / "voice_backends/factory.py").exists()
    assert "VOICE_BACKEND" not in env_source
    assert "OPENAI_REALTIME_FALLBACK_LOCAL" not in env_source
    assert 'export OPENAI_REALTIME_STARTUP_MESSAGE=' in env_source
    assert 'export OPENAI_REALTIME_EXIT_PHRASES=' in env_source
    assert 'export OPENAI_REALTIME_SHUTDOWN_MESSAGE=' in env_source
    assert 'export OPENAI_REALTIME_LOCAL_BARGE_IN=' in env_source
    assert 'export OPENAI_REALTIME_BARGE_IN_MIN_DBFS=' in env_source
    assert 'export WAKEWORD_BACKEND="xvf_vad"' in env_source
    assert 'export XVF_VAD_POLL_S=' in env_source
    assert 'export XVF_VAD_START_FRAMES=' in env_source
    assert 'export XVF_WAKE_PREROLL_S=' in env_source
    assert 'export XVF_WAKE_HOLD_EYELIDS=' in env_source
    assert 'export FACE_TRACKING_PWM_FREQ_HZ=' in env_source
    assert "source env-exports.sh" in readme
    assert "env-openai-realtime.example.sh" not in readme
    assert "VOICE_BACKEND" not in readme


def test_cloud_launcher_reads_startup_and_shutdown_messages_directly() -> None:
    source = _source("coglet-cloud.py")
    assert "os.getenv(\n            os.getenv(" not in source
    assert "_env_text(" in source
    assert '"OPENAI_REALTIME_SHUTDOWN_MESSAGE"' in source
    assert '"OPENAI_REALTIME_STARTUP_MESSAGE"' in source
