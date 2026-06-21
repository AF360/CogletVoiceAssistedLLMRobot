import threading
import time
import struct
import os
import usb.core
import usb.util
import logging


VID = 0x2886
PID = 0x0018

class ReSpeakerMic(threading.Thread):
    def __init__(self, logger=None, debounce_frames=None):
        super().__init__()
        self.logger = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self.daemon = True


        self.is_connected = False
        self.vad_state = False
        self.doa_angle = 0


        self._paused = False


        if debounce_frames is None:
            debounce_frames = int(os.getenv("XVF_VAD_DEBOUNCE_FRAMES", "2"))
        self.debounce_frames = debounce_frames
        self.start_frames = max(1, int(os.getenv("XVF_VAD_START_FRAMES", "3")))
        self.poll_interval_s = max(0.02, float(os.getenv("XVF_VAD_POLL_S", "0.1")))
        self.debug_vad = os.getenv("XVF_VAD_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
        self._speech_counter = 0
        self._silence_counter = 0


        try:
            self.dev = usb.core.find(idVendor=VID)
            if self.dev:
                self.is_connected = True
                self.logger.info(f"[mic] XVF3800 found (0x{VID:04x}) - Hardware VAD/DOA active")
            else:
                self.logger.warning("[mic] XVF3800 NOT found via USB! Fallback to software VAD.")
        except Exception as e:
            self.logger.error(f"[mic] USB Init Error: {e}")
            self.is_connected = False

    def set_paused(self, paused: bool):
        """Pausiert den USB-Abruf, um Bandbreite für Audio-Aufnahme freizugeben."""
        self._paused = paused

    def run(self):
        """Hintergrund-Loop"""
        if not self.is_connected:
            return

        while not self._stop_event.is_set():

            if self._paused:
                time.sleep(0.2)
                continue

            try:

                ret = self.dev.ctrl_transfer(
                    usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                    0, 146, 20, 5, 1000
                )

                payload = struct.unpack('<xHH', ret)
                raw_angle = payload[0]
                raw_vad   = payload[1]
                is_speech = (raw_vad & 1) == 1

                self._apply_vad_sample(raw_angle, is_speech)

            except Exception:
                pass


            time.sleep(self.poll_interval_s)

    def _apply_vad_sample(self, raw_angle, is_speech):
        previous = self.vad_state
        self.doa_angle = raw_angle

        if is_speech:
            self._speech_counter += 1
            self._silence_counter = 0
            if self._speech_counter >= self.start_frames:
                self.vad_state = True
        else:
            self._speech_counter = 0
            self._silence_counter += 1
            if self._silence_counter > self.debounce_frames:
                self.vad_state = False

        if self.debug_vad and self.vad_state != previous:
            self.logger.info("[mic] XVF VAD %s doa=%s", "speech" if self.vad_state else "silence", self.doa_angle)

    def stop(self):
        self._stop_event.set()
        if self.is_connected:
            try:
                usb.util.dispose_resources(self.dev)
            except:
                pass

    def get_status(self):
        return self.vad_state, self.doa_angle
