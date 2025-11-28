"""Tests for the Grove Vision AI client utilities."""

from __future__ import annotations

import pytest

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hardware.grove_vision_ai import FaceDetectionBox, GroveVisionAIClient


class FakeSerial:
    def __init__(self, payloads: list[bytes]) -> None:
        self._payloads = [bytearray(p) for p in payloads]
        self.in_waiting = sum(len(p) for p in self._payloads)
        self.written: list[bytes] = []

    def reset_input_buffer(self) -> None:
        # No-op for tests
        pass

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        return len(data)

    def read(self, size: int) -> bytes:
        if not self._payloads:
            self.in_waiting = 0
            return b""
        chunk = self._payloads[0][:size]
        del self._payloads[0][:size]
        if not self._payloads[0]:
            self._payloads.pop(0)
        self.in_waiting = sum(len(p) for p in self._payloads)
        return bytes(chunk)

    def close(self) -> None:
        pass


def test_face_detection_box_from_list() -> None:
    box = FaceDetectionBox.from_payload([10, 20, 30, 40, 0.75])
    assert box.x == pytest.approx(10.0)
    assert box.width == pytest.approx(30.0)
    assert box.score == pytest.approx(0.75)


def test_face_detection_box_from_dict() -> None:
    box = FaceDetectionBox.from_payload({"x": 5, "y": 6, "w": 7, "h": 8, "confidence": 0.8})
    assert box.height == pytest.approx(8.0)
    assert box.score == pytest.approx(0.8)


def test_invoke_once_parses_boxes() -> None:
    payload = b'{"type":1,"data":{"boxes":[[110,100,20,20,0.9]]}}\r\n'
    client = GroveVisionAIClient(port="/dev/null", serial_instance=FakeSerial([payload]))
    boxes = client.invoke_once(timeout=0.05)
    assert boxes is not None
    assert len(boxes) == 1
    assert boxes[0].x == pytest.approx(110.0)
    assert boxes[0].score == pytest.approx(0.9)
    assert client._serial.written  # type: ignore[attr-defined]
