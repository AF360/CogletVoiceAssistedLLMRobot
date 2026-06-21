import time
import sys
import logging
from hardware.xvf_mic import ReSpeakerMic


MIC_OFFSET = 0

def get_relative_angle(raw_angle, offset):
    """Recalc 0-360° to -180 / +180°, relative to the offset."""
    angle = (raw_angle - offset) % 360
    if angle > 180:
        angle -= 360
    return angle

def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("DOA-Calib")

    print(f"--- DOA calibration (Offset: {MIC_OFFSET}) ---")
    print("Initialize microphone...")

    mic = ReSpeakerMic(logger=logger)
    mic.start()

    print("Mic active. Speak now!")
    print("(Press CTRL+C to exit)")

    try:
        while True:
            is_speech, raw_angle = mic.get_status()

            if is_speech:
                rel_angle = get_relative_angle(raw_angle, MIC_OFFSET)
                pos = int((rel_angle + 180) / 360 * 40)
                pos = max(0, min(40, pos))
                bar = ["-"] * 41
                bar[20] = "|"
                bar[pos] = "O"
                bar_str = "".join(bar)
                print(f"\rRaw: {raw_angle:3d}° | Rel: {rel_angle:4d}° [{bar_str}]", end="")
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        mic.stop()

if __name__ == "__main__":
    main()
