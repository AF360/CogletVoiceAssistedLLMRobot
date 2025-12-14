import threading
import time
import struct
import usb.core
import usb.util
import logging

# Konstanten für XVF3800
VID = 0x2886
PID = 0x0018 

class ReSpeakerMic(threading.Thread):
    def __init__(self, logger=None, debounce_frames=3):
        super().__init__()
        self.logger = logger or logging.getLogger(__name__)
        self._stop_event = threading.Event()
        self.daemon = True
        
        # Status-Variablen
        self.is_connected = False
        self.vad_state = False
        self.doa_angle = 0
        
        # Steuerung
        self._paused = False  # NEU: Pausen-Flag
        
        # Debounce Logik
        self.debounce_frames = debounce_frames
        self._silence_counter = 0

        # USB Device suchen
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
            # NEU: Wenn pausiert, schlafen wir einfach nur und tun nichts am USB-Bus
            if self._paused:
                time.sleep(0.2)
                continue

            try:
                # Register 20 (DOA/VAD) lesen
                ret = self.dev.ctrl_transfer(
                    usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
                    0, 146, 20, 5, 1000 
                )
                
                payload = struct.unpack('<xHH', ret)
                raw_angle = payload[0]
                raw_vad   = payload[1]
                is_speech = (raw_vad & 1) == 1

                self.doa_angle = raw_angle

                if is_speech:
                    self.vad_state = True
                    self._silence_counter = 0
                else:
                    self._silence_counter += 1
                    if self._silence_counter > self.debounce_frames:
                        self.vad_state = False

            except Exception:
                pass
            
            # ÄNDERUNG: 0.5s (2 Hz) reicht zum Drehen und entlastet Wakeword-Engine
            time.sleep(0.5)

    def stop(self):
        self._stop_event.set()
        if self.is_connected:
            try:
                usb.util.dispose_resources(self.dev)
            except:
                pass

    def get_status(self):
        return self.vad_state, self.doa_angle
        
