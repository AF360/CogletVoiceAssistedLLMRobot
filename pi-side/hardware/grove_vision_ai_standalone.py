"""Standalone Grove Vision AI test client for quick hardware checks."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from typing import Iterable

try:
    # allow `python -m pi-side.hardware.grove_vision_ai_standalone`
    from .grove_vision_ai import FaceDetectionBox, GroveVisionAIClient
except ImportError:
    # allow `python grove_vision_ai_standalone.py`
    from grove_vision_ai import FaceDetectionBox, GroveVisionAIClient

logger = logging.getLogger(__name__)

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Trigger inferences on the Grove Vision AI board and print detection results."
        )
    )
    parser.add_argument(
        "--serial-port",
        default="/dev/ttyACM0",
        help="Serial device path where the Grove Vision AI board is attached (default: %(default)s)",
    )
    parser.add_argument(
        "--baud-rate",
        type=int,
        default=921_600,
        help="Baudrate for the serial connection (default: %(default)s)",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=2.0,
        help="Number of inferences per second to trigger (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.4,
        help="Timeout in seconds while waiting for a detection result (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print detection results as JSON instead of a human readable summary",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop after the given number of iterations (0 keeps running until interrupted)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity (default: %(default)s)",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _boxes_to_json(boxes: Iterable[FaceDetectionBox]) -> str:
    payload = {
        "timestamp": time.time(),
        "detections": [
            {
                "x": box.x,
                "y": box.y,
                "width": box.width,
                "height": box.height,
                "score": box.score,
                "center_x": box.center_x,
                "center_y": box.center_y,
            }
            for box in boxes
        ],
    }
    return json.dumps(payload)


def _print_boxes_human_readable(boxes: Iterable[FaceDetectionBox]) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    boxes_list = list(boxes)
    if not boxes_list:
        print(f"[{timestamp}] No detections received from the Grove Vision board.")
        return

    print(f"[{timestamp}] {len(boxes_list)} Detections")
    for idx, box in enumerate(boxes_list, start=1):
        score_display = f"{box.score:.3f}" if box.score is not None else "n/a"
        print(
            f"  #{idx}: score={score_display} x={box.x:.1f} y={box.y:.1f} "
            f"w={box.width:.1f} h={box.height:.1f} cx={box.center_x:.1f} cy={box.center_y:.1f}"
        )


def _run_loop(args: argparse.Namespace) -> int:
    if args.hz <= 0:
        logger.error("--hz must be greater than 0 (recent %.2f)", args.hz)
        return 2

    period = 1.0 / args.hz
    logger.info(
        "Starting Grove Vision AI standalone test: port=%s baud=%d hz=%.2f timeout=%.2f",
        args.serial_port,
        args.baud_rate,
        args.hz,
        args.timeout,
    )

    try:
        client = GroveVisionAIClient(
            port=args.serial_port,
            baudrate=args.baud_rate,
            read_timeout=args.timeout,
        )
    except Exception as exc:  # pragma: no cover - hardware required
        logger.error("Could not open Grove Vision AI board: %s", exc)
        return 1

    stop_requested = False

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        logger.info("Signal %s received, quit.", signum)
        stop_requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    iteration = 0
    try:
        while not stop_requested:
            iteration += 1
            loop_start = time.monotonic()
            boxes = client.invoke_once(timeout=args.timeout)
            if boxes is None:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] No answer (Timeout)")
            else:
                if args.json:
                    print(_boxes_to_json(boxes))
                else:
                    _print_boxes_human_readable(boxes)
            if args.max_iterations and iteration >= args.max_iterations:
                logger.info("Maximum number of iterations (%d) reached", args.max_iterations)
                break
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0.0, period - elapsed)
            time.sleep(sleep_time)
    except KeyboardInterrupt:  # pragma: no cover - manual interruption
        logger.info("Got KeyboardInterrupt, quit.")
    finally:
        client.close()
        logger.info("Serial connection closed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    return _run_loop(args)


if __name__ == "__main__":
    sys.exit(main())
