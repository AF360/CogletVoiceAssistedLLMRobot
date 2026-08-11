from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


PI_SIDE = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (PI_SIDE / name).read_text(encoding="utf-8")


def _load_voice_runtime():
    spec = importlib.util.spec_from_file_location(
        "voice_runtime_under_test", PI_SIDE / "voice_runtime.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voice_launcher_substitutes_runtime_before_local_mode_import() -> None:
    source = _source("coglet-voice.py")
    tree = ast.parse(source)
    statements = [ast.get_source_segment(source, node) or "" for node in tree.body]

    substitution = next(i for i, text in enumerate(statements) if 'sys.modules["robot_runtime"]' in text)
    local_import = next(i for i, text in enumerate(statements) if "import local_mode" in text)

    assert substitution < local_import
    assert "from robot_runtime" not in source
    assert "voice_runtime.get_anim_servo" in source


def test_voice_runtime_has_no_robot_hardware_imports() -> None:
    source = _source("voice_runtime.py")
    ast.parse(source)

    assert "from hardware.robot_runtime" not in source
    assert "import hardware.robot_runtime" not in source
    assert "from hardware.pca9685" not in source
    assert "from hardware.face_tracker" not in source
    assert "from hardware.xvf_mic" not in source


def test_voice_runtime_imports_without_pi_hardware_packages() -> None:
    runtime = _load_voice_runtime()

    assert runtime.XVF_MIC_AVAILABLE is False
    assert runtime.ReSpeakerMic is None
    assert runtime.initialize_all_servos(None) is None
    assert runtime.setup_face_tracking(None, None) is None
    assert runtime.get_anim_servo("MOU") is None


def test_led_is_not_imported_when_disabled(monkeypatch) -> None:
    runtime = _load_voice_runtime()
    monkeypatch.setenv("ENABLE_LED", "0")
    before = set(sys.modules)

    runtime.initialize_status_led()

    assert runtime._status_led is None
    assert "hardware.status_led" not in set(sys.modules) - before
