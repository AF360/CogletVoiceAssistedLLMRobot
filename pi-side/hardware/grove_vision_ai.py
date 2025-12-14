"""Grove Vision AI v2 Face-Detection client utilities."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

from logging_setup import get_logger, setup_logging

setup_logging()
logger = get_logger()

try:  # pragma: no cover - optional dependency in tests
    import serial  # type: ignore
except ImportError:  # pragma: no cover - tests provide fake serial
    serial = None  # type: ignore


@dataclass(slots=True)
class FaceDetectionBox:
    """Represents a single detection result returned by the Vision board."""

    x: float
    y: float
    width: float
    height: float
    score: Optional[float] = None

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0

    @classmethod
    def from_payload(cls, payload: Sequence[float | int | None] | dict) -> "FaceDetectionBox":
        """Create an instance from a payload returned by the board."""

        if isinstance(payload, dict):
            x = float(payload.get("x", 0.0))
            y = float(payload.get("y", 0.0))
            width = float(payload.get("w", payload.get("width", 0.0)))
            height = float(payload.get("h", payload.get("height", 0.0)))
            score_value = payload.get("score", payload.get("confidence"))
            score = float(score_value) if score_value is not None else None
            return cls(x=x, y=y, width=width, height=height, score=score)

        data = list(payload)
        if len(data) < 4:
            raise ValueError("Payload for FaceDetectionBox requires at least 4 entries")
        x, y, width, height, *rest = data
        score = float(rest[0]) if rest else None
        return cls(x=float(x), y=float(y), width=float(width), height=float(height), score=score)


class GroveVisionAIClient:
    """Communicates with the Grove Vision AI v2 board via USB serial."""

    _INVOCATION_COMMAND = b"AT+INVOKE=1,0,0\r"

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 921_600,
        read_timeout: float = 0.0,
        serial_instance: Optional[serial.Serial] = None,
    ) -> None:
        if serial_instance is not None:
            self._serial = serial_instance
        else:
            if serial is None:
                raise RuntimeError("pyserial is required to use GroveVisionAIClient")
            self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=read_timeout)
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._brace_depth = 0

    def close(self) -> None:
        with self._lock:
            try:
                self._serial.close()
            except Exception as exc:  # pragma: no cover - cleanup path
                logger.debug("Failed to close serial port cleanly: %s", exc)

    def invoke_once(self, *, timeout: float = 0.3) -> Optional[List[FaceDetectionBox]]:
        """Trigger a single inference and return detected bounding boxes."""

        deadline = time.monotonic() + timeout
        with self._lock:
            self._flush_input()
            try:
                self._serial.write(self._INVOCATION_COMMAND)
            except Exception as exc:
                logger.error("Failed to send invoke command: %s", exc)
                return None

            while time.monotonic() < deadline:
                boxes = self._read_available()
                if boxes is not None:
                    return boxes
                time.sleep(0.005)
        logger.debug("invoke_once timed out after %.3f s", timeout)
        return None

    # ---------------------------- Internals ----------------------------
    def _flush_input(self) -> None:
        try:
            if hasattr(self._serial, "reset_input_buffer"):
                self._serial.reset_input_buffer()
            else:
                while True:
                    data = self._serial.read(self._serial.in_waiting or 0)
                    if not data:
                        break
        except Exception as exc:
            logger.debug("Failed to flush input buffer: %s", exc)

    def _read_available(self) -> Optional[List[FaceDetectionBox]]:
        try:
            to_read = getattr(self._serial, "in_waiting", 0) or 1
            raw = self._serial.read(to_read)
        except Exception as exc:
            logger.error("Error while reading from Vision board: %s", exc)
            return None
        if not raw:
            return None

        boxes = self._extract_boxes(raw)
        if boxes is None:
            return None
        return boxes

    def _extract_boxes(self, chunk: bytes) -> Optional[List[FaceDetectionBox]]:
        for byte in chunk:
            if byte == ord("{"):
                if self._brace_depth == 0:
                    self._buffer.clear()
                self._brace_depth += 1
                self._buffer.append(byte)
                continue

            if self._brace_depth == 0:
                continue

            self._buffer.append(byte)
            if byte == ord("{"):
                self._brace_depth += 1
            elif byte == ord("}"):
                self._brace_depth -= 1
                if self._brace_depth == 0:
                    try:
                        obj = json.loads(self._buffer.decode("utf-8"))
                    except json.JSONDecodeError:
                        logger.debug("Discarding malformed JSON payload: %r", self._buffer)
                        self._buffer.clear()
                        continue
                    self._buffer.clear()
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("type") != 1:
                        continue
                    data = obj.get("data", {})
                    boxes_raw = data.get("boxes", [])
                    boxes = [FaceDetectionBox.from_payload(entry) for entry in boxes_raw]
                    return boxes
        return None


__all__ = ["FaceDetectionBox", "GroveVisionAIClient"]
