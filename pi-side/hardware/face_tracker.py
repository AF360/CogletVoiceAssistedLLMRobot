"""
Face tracking logic for Coglet (eyes, head pitch, wheels/base follow).

This module is partly derived from and inspired by Will Cogley's
face-tracking mini bot reference code (main.py), in particular:

- the error-centric tracking loop for eyes and head pitch with deadzones
- the delayed base/body rotation after eye deviation passes a threshold
- the non-linear power-map remapping of offsets using an exponent curve

Original work by Will Cogley, used under the Creative Commons
Attribution-NonCommercial-ShareAlike 4.0 International License (CC BY-NC-SA 4.0).

Original: main.py from Will Cogley's Halloween-watcher reference code
License: https://creativecommons.org/licenses/by-nc-sa/4.0/
Modifications and refactoring for Coglet by Andreas Fatum, 2025.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Generator, Iterable, Optional, Sequence, Tuple

from hardware.pca9685_servo import Servo

from .grove_vision_ai import FaceDetectionBox, GroveVisionAIClient
from logging_setup import get_logger, setup_logging

setup_logging()
logger = get_logger()


@dataclass(frozen=True)
class FaceTrackingServos:
    """Bundle of servos that should follow the detected face."""

    eyes: Tuple[Servo, ...]
    yaw: Optional[Servo] = None
    pitch: Optional[Servo] = None
    wheels: Tuple[Servo, ...] = ()

    def all_servos(self) -> Iterable[Servo]:
        """All servos whose physics should be ticked regularly via update(dt)."""
        yield from self.eyes
        if self.yaw is not None:
            yield self.yaw
        if self.pitch is not None:
            yield self.pitch
        yield from self.wheels


@dataclass(frozen=True)
class FaceTrackingConfig:
    """Tunable parameters to map detection coordinates to servo movements."""

    frame_width: float = 220.0
    frame_height: float = 200.0
    coordinates_are_center: bool = True
    eye_deadzone_px: float = 10.0
    yaw_deadzone_px: float = 18.0
    pitch_deadzone_px: float = 18.0
    eye_gain_deg_per_px: float = 0.08
    yaw_gain_deg_per_px: float = 0.05
    pitch_gain_deg_per_px: float = 0.06
    eye_max_delta_deg: float = 20.0
    yaw_max_delta_deg: float = 30.0
    pitch_max_delta_deg: float = 20.0
    invoke_interval_s: float = 0.15
    invoke_timeout_s: float = 0.25
    update_interval_s: float = 0.02
    neutral_timeout_s: float = 2.0
    wheel_deadzone_deg: float = 5.0
    wheel_follow_delay_s: float = 0.8
    wheel_input_min_deg: float = 30.0
    wheel_input_max_deg: float = 150.0
    wheel_output_min_deg: float = 80.0
    wheel_output_max_deg: float = 100.0
    wheel_power: float = 2.0
    
    # --- Patrol / Idle Scanning ---
    patrol_enabled: bool = True
    patrol_interval_s: float = 30.0   # Wie oft suchen wir?
    patrol_range_wheels_deg: float = 40.0 # Wie weit drehen wir uns (Relativ zur Mitte)
    patrol_range_eyes_deg: float = 25.0   # Wie weit schauen die Augen
    patrol_range_pitch_deg: float = 15.0  # Wie weit nicken wir

    @property
    def frame_center_x(self) -> float:
        return self.frame_width / 2.0

    @property
    def frame_center_y(self) -> float:
        return self.frame_height / 2.0


class FaceTracker:
    """Runs a background loop to follow faces with the connected servos."""

    def __init__(
        self,
        client: GroveVisionAIClient,
        servos: FaceTrackingServos,
        *,
        config: Optional[FaceTrackingConfig] = None,
    ) -> None:
        if not servos.eyes:
            raise ValueError("At least one eye servo is required for face tracking")
        self._client = client
        self._servos = servos
        self._config = config or FaceTrackingConfig()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_detection: float = 0.0
        self._last_face: Optional[FaceDetectionBox] = None
        self._lock = threading.Lock()
        self._wheel_trigger_time: Optional[float] = None
        self._wheel_active = False
        self._patrol_gen: Optional[Generator[None, None, None]] = None
        self._last_patrol_finish = time.monotonic()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="FaceTracker", daemon=True)
        self._thread.start()
        logger.info("Face tracker thread started (patrol=%s, interval=%.1fs)", 
                    self._config.patrol_enabled, self._config.patrol_interval_s)

    def stop(self, *, join_timeout: float = 1.0) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=join_timeout)
        self._thread = None
        logger.info("Face tracker thread stopped")

    # ---------------------------- Internals ----------------------------
    def _run(self) -> None:
        cfg = self._config
        next_invoke = 0.0
        last_update = time.monotonic()
        
        # Reset timestamps
        self._last_detection = time.monotonic()
        self._last_patrol_finish = time.monotonic()

        while not self._stop_event.is_set():
            now = time.monotonic()
            dt = now - last_update
            last_update = now
            self._update_servos(dt)

            if now >= next_invoke:
                # 1. Vision Check
                boxes = self._client.invoke_once(timeout=cfg.invoke_timeout_s)
                
                if boxes:
                    # GESICHT GEFUNDEN!
                    self._handle_detection(boxes, timestamp=now)
                    # Patrol sofort abbrechen
                    self._patrol_gen = None
                    self._last_patrol_finish = now # Reset timer
                
                else:
                    # KEIN GESICHT
                    
                    # Wenn wir noch in der Neutral-Timeout-Phase sind (User gerade weg):
                    # -> Zurück zur Mitte.
                    # Wenn wir Patrouille fahren -> Nicht stören, Generator weitermachen lassen.
                    
                    time_since_detection = now - self._last_detection
                    
                    if self._patrol_gen is not None:
                        # Wir sind mitten in einer Patrouille -> weitermachen
                        try:
                            next(self._patrol_gen)
                        except StopIteration:
                            # Fertig
                            self._patrol_gen = None
                            self._last_patrol_finish = now
                            logger.debug("Patrol finished")
                            # Zurück zu Neutral sicherstellen
                            self._move_all_to_neutral()
                            
                    elif time_since_detection > cfg.neutral_timeout_s:
                        # Wir sind im "Idle" Mode.
                        
                        # Prüfen ob Zeit für Patrouille
                        time_since_patrol = now - self._last_patrol_finish
                        if cfg.patrol_enabled and time_since_patrol > cfg.patrol_interval_s:
                            logger.info("Starting patrol scan...")
                            self._patrol_gen = self._create_patrol_sequence()
                        else:
                            # Einfach nur warten / Neutral halten
                            self._handle_missing_detection(now)
                    
                    else:
                        # Noch innerhalb des Timeouts -> Neutral anfahren
                        self._handle_missing_detection(now)

                next_invoke = now + cfg.invoke_interval_s

            time.sleep(cfg.update_interval_s)

    def _update_servos(self, dt: float) -> None:
        for servo in self._servos.all_servos():
            servo.update(dt)

    def _move_all_to_neutral(self):
        """Force all tracking servos to neutral immediately."""
        for servo in self._servos.all_servos():
            servo.move_to(servo.config.neutral_deg)

    # --- Patrol Sequence Generator (Modified for "Eyes Lead" behavior) ---
    # --- Patrol Sequence Generator (Corrected: Wheels+=Left, Eyes-=Left) ---
    def _create_patrol_sequence(self) -> Generator[None, None, None]:
        cfg = self._config
        
        def wait_seconds(sec: float):
            end = time.monotonic() + sec
            while time.monotonic() < end:
                yield

        def set_pose(wheel_offset=0.0, eye_offset=0.0, pitch_offset=0.0):
            # Wheels
            for w in self._servos.wheels:
                w.move_to(w.config.neutral_deg + wheel_offset)
            # Eyes
            for e in self._servos.eyes:
                e.move_to(e.config.neutral_deg + eye_offset)
            # Pitch
            if self._servos.pitch:
                p = self._servos.pitch
                p.move_to(p.config.neutral_deg + pitch_offset)

        # ==========================================
        # PHASE 1: NACH LINKS (Räder +, Augen -)
        # ==========================================
        
        # A. Augen führen: Blick nach LINKS (-)
        set_pose(wheel_offset=0.0, 
                 eye_offset=-cfg.patrol_range_eyes_deg)
        yield from wait_seconds(0.5)

        # B. Körper folgt: Drehung nach LINKS (+)
        set_pose(wheel_offset=cfg.patrol_range_wheels_deg, 
                 eye_offset=-cfg.patrol_range_eyes_deg)
        yield from wait_seconds(2.0)

        # C. Nicken (Check)
        set_pose(wheel_offset=cfg.patrol_range_wheels_deg, 
                 eye_offset=-cfg.patrol_range_eyes_deg,
                 pitch_offset=10.0)
        yield from wait_seconds(0.4)
        set_pose(wheel_offset=cfg.patrol_range_wheels_deg, 
                 eye_offset=-cfg.patrol_range_eyes_deg,
                 pitch_offset=0.0)
        yield from wait_seconds(0.4)

        # ==========================================
        # PHASE 2: NACH RECHTS (Räder -, Augen +)
        # ==========================================

        # A. Augen führen: Blick nach RECHTS (+)
        # Körper steht noch LINKS (+)
        set_pose(wheel_offset=cfg.patrol_range_wheels_deg, 
                 eye_offset=cfg.patrol_range_eyes_deg)
        yield from wait_seconds(0.6)

        # B. Körper folgt: Drehung nach RECHTS (-)
        set_pose(wheel_offset=-cfg.patrol_range_wheels_deg, 
                 eye_offset=cfg.patrol_range_eyes_deg)
        yield from wait_seconds(3.0) # Langer Weg

        # C. Nicken
        set_pose(wheel_offset=-cfg.patrol_range_wheels_deg, 
                 eye_offset=cfg.patrol_range_eyes_deg,
                 pitch_offset=10.0)
        yield from wait_seconds(0.4)
        set_pose(wheel_offset=-cfg.patrol_range_wheels_deg, 
                 eye_offset=cfg.patrol_range_eyes_deg,
                 pitch_offset=0.0)
        yield from wait_seconds(0.4)

        # ==========================================
        # PHASE 3: ZURÜCK ZUR MITTE
        # ==========================================

        # A. Augen zur Mitte (0.0)
        # Körper steht noch RECHTS (-)
        set_pose(wheel_offset=-cfg.patrol_range_wheels_deg, 
                 eye_offset=0.0)
        yield from wait_seconds(0.5)

        # B. Körper zur Mitte
        set_pose(wheel_offset=0.0, eye_offset=0.0)
        yield from wait_seconds(1.5)
    
    def _handle_detection(self, boxes: Sequence[FaceDetectionBox], *, timestamp: float) -> None:
        best = self._select_best_box(boxes)
        if best is None:
            self._handle_missing_detection(timestamp)
            return

        cfg = self._config
        cx, cy = self._extract_center(best)
        error_x = cx - cfg.frame_center_x
        error_y = cy - cfg.frame_center_y

        with self._lock:
            eye_targets_before = tuple(servo.target_deg for servo in self._servos.eyes)
            for servo in self._servos.eyes:
                if abs(error_x) > cfg.eye_deadzone_px:
                    delta = self._clamp(error_x * cfg.eye_gain_deg_per_px, cfg.eye_max_delta_deg)
                    servo.move_to(servo.target_deg + delta)
            if self._servos.yaw is not None and abs(error_x) > cfg.yaw_deadzone_px:
                delta = self._clamp(error_x * cfg.yaw_gain_deg_per_px, cfg.yaw_max_delta_deg)
                self._servos.yaw.move_to(self._servos.yaw.target_deg + delta)
            if self._servos.pitch is not None and abs(error_y) > cfg.pitch_deadzone_px:
                delta = self._clamp(error_y * cfg.pitch_gain_deg_per_px, cfg.pitch_max_delta_deg)
                self._servos.pitch.move_to(self._servos.pitch.target_deg + delta)
            self._update_wheels(timestamp, error_x)
            self._last_detection = timestamp
            self._last_face = best
            if logger.isEnabledFor(logging.DEBUG):
                # logger.debug("Face det...") # optional reduce spam
                pass

    def _handle_missing_detection(self, now: float) -> None:
        if (now - self._last_detection) < self._config.neutral_timeout_s:
            return
        
        # Nur auf Neutral gehen, wenn KEINE Patrouille läuft
        if self._patrol_gen is None:
            for servo in self._servos.all_servos():
                servo.move_to(servo.config.neutral_deg)
            self._reset_wheel_follow()
        
        self._last_face = None

    def _select_best_box(self, boxes: Sequence[FaceDetectionBox]) -> Optional[FaceDetectionBox]:
        if not boxes:
            return None
        return max(
            boxes,
            key=lambda box: ((box.score or 0.0), box.width * box.height),
        )

    def _extract_center(self, box: FaceDetectionBox) -> Tuple[float, float]:
        if self._config.coordinates_are_center:
            return box.x, box.y
        return box.center_x, box.center_y

    @staticmethod
    def _clamp(value: float, max_delta: float) -> float:
        return max(-max_delta, min(max_delta, value))

    def _average_eye_target(self) -> float:
        if not self._servos.eyes:
            return 0.0
        return sum(servo.target_deg for servo in self._servos.eyes) / len(self._servos.eyes)

    def _update_wheels(self, timestamp: float, error_x: float) -> None:
        wheels = self._servos.wheels
        if not wheels:
            return
        cfg = self._config
        if abs(error_x) <= cfg.eye_deadzone_px:
            self._reset_wheel_follow()
            return
        eye_target = self._average_eye_target()
        eye_neutral = sum(servo.config.neutral_deg for servo in self._servos.eyes) / len(self._servos.eyes)
        deviation = abs(eye_target - eye_neutral)
        if deviation <= cfg.wheel_deadzone_deg:
            self._reset_wheel_follow()
            return
        if self._wheel_trigger_time is None:
            self._wheel_trigger_time = timestamp
        if (timestamp - self._wheel_trigger_time) < cfg.wheel_follow_delay_s:
            return
        target = self._map_eye_to_wheel_target(eye_target)
        for wheel in wheels:
            wheel.move_to(target)
        self._wheel_active = True

    def _reset_wheel_follow(self) -> None:
        wheels = self._servos.wheels
        if not wheels:
            return
        self._wheel_trigger_time = None
        if self._wheel_active:
            for wheel in wheels:
                wheel.move_to(wheel.config.neutral_deg)
            self._wheel_active = False

    def _map_eye_to_wheel_target(self, eye_target: float) -> float:
        cfg = self._config
        return self._power_map(
            eye_target,
            cfg.wheel_input_min_deg,
            cfg.wheel_input_max_deg,
            cfg.wheel_output_min_deg,
            cfg.wheel_output_max_deg,
            cfg.wheel_power,
        )

    @staticmethod
    def _power_map(
        value: float,
        in_min: float,
        in_max: float,
        out_min: float,
        out_max: float,
        power: float,
    ) -> float:
        """Non-linear remapping of offsets (adapted from Will Cogley's mini bot code)."""
        if in_max == in_min:
            return (out_min + out_max) / 2.0
        norm = (value - in_min) / (in_max - in_min)
        norm = max(0.0, min(1.0, norm))
        norm = (norm * 2.0) - 1.0
        curved = (abs(norm) ** power) * (1.0 if norm >= 0.0 else -1.0)
        curved = (curved + 1.0) / 2.0
        return out_min + (out_max - out_min) * curved


__all__ = ["FaceTracker", "FaceTrackingServos", "FaceTrackingConfig"]
