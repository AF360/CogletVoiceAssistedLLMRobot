#!/usr/bin/env python3
import time
from status_led import StatusLED, CogletState
from logging_setup import get_logger, setup_logging

setup_logging()
logger = get_logger()


def main() -> None:
    led = StatusLED()

    try:
        while True:
            logger.info("State: AWAIT_WAKEWORD (yellow)")
            led.set_state(CogletState.AWAIT_WAKEWORD)
            time.sleep(2)

            logger.info("State: LISTENING (red)")
            led.set_state(CogletState.LISTENING)
            time.sleep(2)

            logger.info("State: THINKING (blue)")
            led.set_state(CogletState.THINKING)
            time.sleep(2)

            logger.info("State: SPEAKING (green)")
            led.set_state(CogletState.SPEAKING)
            time.sleep(2)

    except KeyboardInterrupt:
        logger.info("Exit demo, LED switched off.")
        led.off()


if __name__ == "__main__":
    main()
