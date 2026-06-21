#!/usr/bin/env python3
import logging
import time

import board
import busio
from adafruit_pca9685 import PCA9685

from hardware.servo_presets import SERVO_LAYOUT_V1
from hardware.pca9685_servo import Servo
from hardware.servo_calibration import load_servo_calibration, apply_calibration_to_config

INTERESTING_SERVOS = ["LID", "MOU", "NPT", "NRL", "EAL", "EAR"]

def build_servo(name: str, pca: PCA9685, calibration_map):
    layout_def = SERVO_LAYOUT_V1[name]
    channel = layout_def.channel
    base_cfg = layout_def.config

    calibration = calibration_map.get(channel)
    if calibration is not None:
        cfg = apply_calibration_to_config(base_cfg, calibration)
    else:
        cfg = base_cfg

    servo = Servo(pca.channels[channel], config=cfg)
    return servo

def move_smooth(servo: Servo, target_deg: float, steps: int = 10, dt: float = 0.05):
    servo.move_to(target_deg)
    for _ in range(steps):
        servo.update(dt)
        time.sleep(dt)


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("servo-calib")

    calibration_map, path = load_servo_calibration(logger)
    if path:
        logger.info("Using servo calibration from %s", path)
    else:
        logger.info("No servo calibration file found; using layout defaults")

    i2c = busio.I2C(board.SCL, board.SDA)
    pca = PCA9685(i2c)
    pca.frequency = 50

    try:
        servos = {}
        for name in INTERESTING_SERVOS:
            servos[name] = build_servo(name, pca, calibration_map)

        while True:
            print("\n=== Servo orientation calibration ===")
            for i, name in enumerate(INTERESTING_SERVOS, 1):
                layout_def = SERVO_LAYOUT_V1[name]
                print(f"{i}) {name} (Channel {layout_def.channel})")
            print("q) Quit")

            choice = input("> ").strip().lower()
            if choice in ("q", "quit", "exit"):
                break

            try:
                idx = int(choice) - 1
            except ValueError:
                continue
            if not (0 <= idx < len(INTERESTING_SERVOS)):
                continue

            name = INTERESTING_SERVOS[idx]
            servo = servos[name]
            cfg = servo.config
            channel = SERVO_LAYOUT_V1[name].channel

            print(f"\n*** Testing Servo {name} (Channel {channel}) ***")
            print(f"  min={cfg.min_angle_deg:.1f}°, neutral={cfg.neutral_deg:.1f}°, max={cfg.max_angle_deg:.1f}°")
            input("Enter → move to neutral-position")
            move_smooth(servo, cfg.neutral_deg)

            input("Enter → move to Min-position (observe direction)")
            move_smooth(servo, cfg.min_angle_deg)

            input("Enter → move to Max-position (oberserve direction)")
            move_smooth(servo, cfg.max_angle_deg)

            input("Enter → back to neutral position")
            move_smooth(servo, cfg.neutral_deg)

            print("\nTake notes for this servo:")
            if name == "NPT":
                print("  - MIN: Coglets nose going down towards the ground are up towards the sky?")
                print("  - MAX: Coglets nose going down towards the ground are up towards the sky?")
            elif name == "NRL":
                print("  - MIN: Coglets head rolled towards his left shoulder or towrds his right shoulder?")
                print("  - MAX: Coglets head rolled towards his left shoulder or towrds his right shoulder?")
            elif name == "LID":
                print("  - MIN/MAX: which direction is lid open, which is lid closed?")
            elif name == "MOU":
                print("  - MIN/MAX: Mouth more closed or more open?")
            else:
                print("  - Which pose is MIN/NEUTRAL/MAX (Ear to front/back?")

    finally:
        pca.deinit()


if __name__ == "__main__":
    main()
