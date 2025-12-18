#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import queue
import subprocess as sp
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from logging_setup import get_logger, setup_logging

# MQTT: prefer v2, fall back to v1 (avoids DeprecationWarning on v2)
try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.client import CallbackAPIVersion  # available in v2?
    MQTT_USE_V2 = True
except Exception:
    import paho.mqtt.client as mqtt  # type: ignore
    CallbackAPIVersion = None
    MQTT_USE_V2 = False

# ============ Env ============
PIPER_BIN  = os.getenv("PIPER_BIN", "/opt/piper/piper")
PIPER_MODEL= os.getenv("PIPER_MODEL", "/opt/piper/voices/de_DE-karlsson-low.onnx")
PIPER_CFG  = os.getenv("PIPER_CFG",   "/opt/piper/voices/de_DE-karlsson-low.onnx.json")
SENT_SIL   = os.getenv("PIPER_SENTENCE_SILENCE", "0.06")

SPEAKER    = os.getenv("SPEAKER_DEVICE", "spk")

MQTT_HOST  = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT  = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER  = os.getenv("MQTT_USER") or None
MQTT_PASS  = os.getenv("MQTT_PASS") or None
MQTT_BASE  = os.getenv("MQTT_BASE", "coglet/tts")
MQTT_CMD_QOS = 1  # say/cancel immer QoS1 (Dedup via ID)
MQTT_STATUS_QOS = 0  # Status-Events schnell & leichtgewichtig
MQTT_FORCE_V311 = os.getenv("MQTT_FORCE_V311", "0").lower() in {"1", "true", "yes", "on"}
if hasattr(mqtt, "MQTTv5") and not MQTT_FORCE_V311:
    MQTT_PROTOCOL = mqtt.MQTTv5
else:
    MQTT_PROTOCOL = getattr(mqtt, "MQTTv311", 4)

OUT_DIR    = Path("/run/piper/out")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TOPIC_SAY     = f"{MQTT_BASE}/say"
TOPIC_CANCEL  = f"{MQTT_BASE}/cancel"
TOPIC_STATUS  = f"{MQTT_BASE}/status"     # JSON: {"state":"READY|START|SPEAKING|DONE|CANCELLED|ERROR","id":"..."}

setup_logging()
logger = get_logger()

# ============ Piper persistent wrapper ============
class PiperPersistent:
    """
    Start 'piper' ONCE with --output_wav <DIR>.
    For each line of text, Piper produces a WAV file and writes its path (ending with .wav) to stdout as a line.
    """
    def __init__(self, bin_path, model, cfg, sentence_sil="0.06", out_dir=OUT_DIR):
        self.bin = bin_path
        self.model = model
        self.cfg = cfg
        self.sil = str(sentence_sil)
        self.out_dir = str(out_dir)

        # Force CWD into out_dir (avoids quirks like "Output directory: /."))
        try:
            os.chdir(self.out_dir)
        except Exception as e:
            logger.error("[piper] Error changing directory: %s", e)
        except Exception as e:
            logger.warning("[piper] Error reading stderr: %s", e)
        except Exception as e:
            logger.warning("[piper] Error killing process: %s", e)

        cmd = [
            self.bin,
            "--model", self.model,
            "--config", self.cfg,
            "--sentence_silence", self.sil,
            "--output_wav", self.out_dir  # DIRECTORY-MODUS
        ]
        logger.debug("[piper] spawn: %s", " ".join(cmd))
        self.proc = sp.Popen(
            cmd,
            stdin=sp.PIPE,
            stdout=sp.PIPE,   # text lines with *.wav
            stderr=sp.PIPE,   # Piper logs
            text=True,
            bufsize=1
        )
        self._stderr_t = threading.Thread(target=self._stderr_reader, daemon=True)
        self._stderr_t.start()
        self._lock = threading.Lock()  # read exactly one response line per request

    def _stderr_reader(self):
        try:
            for line in self.proc.stderr:
                line = line.rstrip("\n")
                if not line:
                    continue
                low = line.lower()
                if " [error]" in low:
                    logger.error("[piper] %s", line)
                elif " [warning]" in low:
                    logger.warning("[piper] %s", line)
                elif "real-time factor" in low:
                    logger.debug("[piper] %s", line)  # log noisy metrics only on DEBUG
                else:
                    logger.info("[piper] %s", line)
        except Exception as e:
            logger.warning("[piper] Error reading stderr: %s", e)
    
    def is_alive(self):
        return self.proc and (self.proc.poll() is None)

    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1.0)
                except sp.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass

    def synth_one(self, text, timeout_sec=20.0):
        """Send one line of text, wait for a *.wav line on stdout, and return its path."""
        if not self.is_alive():
            raise RuntimeError("piper not running")

        with self._lock:
            # 1) Text senden
            try:
                self.proc.stdin.write(text.replace("\n", " ") + "\n")
                self.proc.stdin.flush()
            except Exception as e:
                raise RuntimeError(f"write failed: {e}")

            # 2) Read one *.wav line
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    time.sleep(0.01)
                    continue
                line = line.strip()
                if line.endswith(".wav") and line.startswith("/"):
                    return line
            raise TimeoutError("no wav path from piper")

# ============ Player (start/wait separated for SPEAKING event) ============
class Player:
    def __init__(self, device=SPEAKER):
        self.device = device
        self._lock = threading.Lock()
        self._proc = None

    def start(self, wav_path):
        with self._lock:
            self.stop()  # sicherheitshalber
            cmd = ["aplay", "-q", "-D", self.device, "-t", "wav", wav_path]
            logger.debug("[piper] aplay: %s", " ".join(cmd))
            self._proc = sp.Popen(cmd, stdin=None, stdout=sp.DEVNULL, stderr=sp.PIPE, text=False)

    def wait(self):
        with self._lock:
            p = self._proc
        if not p:
            return False
        rc = p.wait()
        with self._lock:
            self._proc = None
        return rc == 0

    def stop(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=0.5)
                except sp.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass
            finally:
                self._proc = None

# ============ MQTT + Worker ============
say_q: queue.Queue[tuple[str, str]] = queue.Queue()
current_id_lock = threading.Lock()
current_id: Optional[str] = None
recent_ids_lock = threading.Lock()
recent_ids: dict[str, float] = {}


def _remember_id(eid: str) -> bool:
    """Deduplication helper: returns False if ID already processed."""
    now = time.time()
    with recent_ids_lock:
        # Cleanup: drop IDs older than 60s
        for key, ts in list(recent_ids.items()):
            if now - ts > 60.0:
                recent_ids.pop(key, None)
        if eid in recent_ids:
            return False
        recent_ids[eid] = now
        # Limit memory (max 256 IDs)
        if len(recent_ids) > 256:
            oldest = min(recent_ids.items(), key=lambda item: item[1])[0]
            recent_ids.pop(oldest, None)
        return True

def publish_status(client: mqtt.Client, state: str, eid: Optional[str] = None,
                   extra: Optional[dict[str, Any]] = None, retain: bool = False) -> None:
    payload: dict[str, Any] = {"state": state}
    if eid:
        payload["id"] = eid
    if extra:
        payload.update(extra)
    client.publish(
        TOPIC_STATUS,
        json.dumps(payload, ensure_ascii=False),
        qos=MQTT_STATUS_QOS,
        retain=retain,
    )

def _remove_pending(eid: str) -> bool:
    """Remove entries with a matching ID from the queue."""
    removed = False
    with say_q.mutex:  # type: ignore[attr-defined]
        items = list(say_q.queue)  # type: ignore[attr-defined]
        say_q.queue.clear()  # type: ignore[attr-defined]
        for item in items:
            if item[0] == eid:
                removed = True
                continue
            say_q.queue.append(item)  # type: ignore[attr-defined]
    return removed


def _parse_cancel_payload(payload_str: str) -> Optional[str]:
    payload_str = payload_str.strip()
    if not payload_str:
        return None
    if payload_str.startswith("{"):
        try:
            obj = json.loads(payload_str)
            candidate = obj.get("id") or obj.get("target")
            if candidate:
                return str(candidate)
        except Exception:
            logger.warning("[piper] cancel payload JSON parsing failed; falling back to raw text")
    return payload_str or None


def _handle_message(topic, payload_str, userdata):
    if topic == TOPIC_SAY:
        text, eid = None, None
        if payload_str.startswith("{"):
            try:
                obj = json.loads(payload_str)
                text = obj.get("text") or ""
                eid  = obj.get("id")   or None
            except Exception:
                text = payload_str
        else:
            text = payload_str
        if not text:
            return
        if not eid:
            eid = str(int(time.time() * 1000))
        if not _remember_id(eid):
            logger.info("[piper] duplicate say ignored id=%s", eid)
            return
        say_q.put((eid, text))

    elif topic == TOPIC_CANCEL:
        client = userdata.get("client")
        target = _parse_cancel_payload(payload_str)
        cancelled = False
        active_id: Optional[str]
        with current_id_lock:
            active_id = current_id
        if target is None:
            if active_id:
                logger.warning("[piper] cancel without id → applying to current utterance")
                target = active_id
            else:
                logger.warning("[piper] cancel without id ignored (no active utterance)")
                return

        if target and active_id and target == active_id:
            userdata["cancel_flag"][0] = True
            userdata["player"].stop()
            cancelled = True

        if target and _remove_pending(target):
            cancelled = True
            if client is not None:
                publish_status(client, "CANCELLED", target)

        if not cancelled and target:
            logger.info("[piper] cancel ignored – id %s not active or queued", target)

# v2 callbacks (preferred)
def on_mqtt_connect_v2(client, userdata, flags, reason_code, properties):
    logger.info("[piper] mqtt connected rc=%s", reason_code)
    client.subscribe(TOPIC_SAY, qos=MQTT_CMD_QOS)
    client.subscribe(TOPIC_CANCEL, qos=MQTT_CMD_QOS)
    publish_status(client, "READY", retain=True)

def on_mqtt_message_v2(client, userdata, message):
    topic = message.topic
    payload = message.payload.decode("utf-8", errors="ignore").strip()
    _handle_message(topic, payload, userdata)

# v1 callbacks (fallback)
def on_mqtt_connect_v1(client, userdata, flags, rc):
    logger.info("[piper] mqtt connected rc=%s", rc)
    client.subscribe(TOPIC_SAY, qos=MQTT_CMD_QOS)
    client.subscribe(TOPIC_CANCEL, qos=MQTT_CMD_QOS)
    publish_status(client, "READY", retain=True)

def on_mqtt_message_v1(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore").strip()
    _handle_message(topic, payload, userdata)

def worker_loop(client: mqtt.Client, piper: PiperPersistent, player: Player, cancel_flag):
    global current_id
    while True:
        eid, text = say_q.get()
        try:
            with current_id_lock:
                current_id = eid
            publish_status(client, "START", eid)

            # Reset cancel flag before a new sentence
            cancel_flag[0] = False

            # 1) TTS (warm)
            synth_start = time.perf_counter()
            wav_path = piper.synth_one(text, timeout_sec=30.0)
            synth_end = time.perf_counter()

            if cancel_flag[0]:
                # Cancel during synthesis -> remove file and report status here
                try: os.remove(wav_path)
                except Exception: pass
                publish_status(client, "CANCELLED", eid)
                logger.info(
                    "[piper] id=%s cancelled during synth (synth=%.3fs)",
                    eid,
                    synth_end - synth_start,
                )
                continue

            # 2) Playback
            player.start(wav_path)
            publish_status(client, "SPEAKING", eid)   # <<< SPEAKING state emitted here
            speak_start = time.perf_counter()
            ok = player.wait()
            speak_end = time.perf_counter()
            try: os.remove(wav_path)
            except Exception: pass

            if cancel_flag[0]:
                publish_status(client, "CANCELLED", eid)  # emit once here only
                logger.info(
                    "[piper] id=%s cancelled during playback (synth=%.3fs, play=%.3fs)",
                    eid,
                    synth_end - synth_start,
                    speak_end - speak_start,
                )
            else:
                if ok:
                    publish_status(client, "DONE", eid)
                    logger.info(
                        "[piper] id=%s finished (synth=%.3fs, play=%.3fs, total=%.3fs)",
                        eid,
                        synth_end - synth_start,
                        speak_end - speak_start,
                        speak_end - synth_start,
                    )
                else:
                    publish_status(client, "ERROR", eid, {"reason":"aplay_failed"})
                    logger.error(
                        "[piper] id=%s playback failed (synth=%.3fs, play=%.3fs)",
                        eid,
                        synth_end - synth_start,
                        speak_end - speak_start,
                    )

        except Exception as e:
            publish_status(client, "ERROR", eid, {"reason": str(e)})
            logger.error("[piper] id=%s failed: %s", eid, e)
        finally:
            with current_id_lock:
                current_id = None

def main():
    # Start persistent Piper instance
    piper = PiperPersistent(PIPER_BIN, PIPER_MODEL, PIPER_CFG, SENT_SIL, OUT_DIR)
    if not piper.is_alive():
        logger.error("[piper] failed to start")
        sys.exit(1)

    player = Player(SPEAKER)

    # MQTT client
    cancel_flag = [False]   # mutable container
    userdata = {"player": player, "cancel_flag": cancel_flag}
    client_kwargs: dict[str, Any] = {"userdata": userdata, "protocol": MQTT_PROTOCOL}
    if MQTT_USE_V2 and CallbackAPIVersion is not None:
        client_kwargs["callback_api_version"] = CallbackAPIVersion.VERSION2
    client = mqtt.Client(**client_kwargs)
    userdata["client"] = client

    if MQTT_USE_V2 and CallbackAPIVersion is not None:
        client.on_connect = on_mqtt_connect_v2
        client.on_message = on_mqtt_message_v2
    else:
        client.on_connect = on_mqtt_connect_v1
        client.on_message = on_mqtt_message_v1

    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    # Last-Will: OFFLINE retained so clients notice outages
    try:
        client.will_set(
            TOPIC_STATUS,
            json.dumps({"state": "OFFLINE"}),
            qos=MQTT_STATUS_QOS,
            retain=True,
        )
    except Exception as e:
        logger.warning("[piper] failed to set LWT: %s", e)

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)

    # Worker thread
    t = threading.Thread(target=worker_loop, args=(client, piper, player, cancel_flag), daemon=True)
    t.start()

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        player.stop()
        piper.close()

if __name__ == "__main__":
    main()

