# Raspberry Pi side setup & code


## Files
- `coglet-pi.py` — Python entrypoint on the Pi that orchestrates the Listen->Detect->Record->TTS->LLM->STT loop
- `env-exports.sh` — Environment variables and switches for the project
- `piper_mqtt_tts` — Example `/etc/default/piper_mqtt_tts` environment file for the Piper TTS MQTT bridge on the Pi
- `piper_mqtt_tts.py` — Piper text-to-speech bridge: subscribes to MQTT `.../say` / `.../cancel`, runs Piper CLI and publishes status events
- `piper_mqtt_tts.service` — systemd service definition for the MQTT-based Piper TTS bridge
- `say` — Script that sends text to the Piper TTS MQTT bridge; copy to `/usr/local/bin/say`
- `say-cancel` — Script that cancels speech output via MQTT; copy to `/usr/local/bin/say-cancel`
- `coglet.onnx` OpenWakeWord model for the wakeword "Coglet" (Important note: The model has been trained with data of a mixture of different licenses and usage restrictions. As such, any custom models trained with this data (like this model) should be considered as appropriate for non-commercial personal use only.
- `requirements.txt` — Python dependencies
- `README.md` — This file
- `hardware` subdirectory — Hardware-related code (PCA9685, ReSpeaker XVF3800, Grove Vision AI v2, WS281x RGB LED)

## Start
```bash
coglet_pi.py will run as a service on the Pi
manual start: 
source /opt/cogket-pi/.venv/bin/activate; 
cd /opt/coglet-pi; 
source env-exports.sh
python ./coglet-pi.py
```
`coglet-pi.py`:
- Waits for the wake word (openwakeword).
- On successful detection it plays a spoken “Yes?” via the Piper TTS server, signaling that a question can be asked.
- Starts recording (begin speaking within 1.5 seconds after the wake-word confirmation) until silence is detected by WebRTC VAD.
- Sends the recorded `.wav` file to the Faster-Whisper server on port 5005 and receives the transcribed text.
- Sends the text to the Ollama server on port 11434 and receives the LLM response.
- Sends the LLM response to the Piper server on the Pi for playback through the USB speaker.
- After playback the Pi enters “conversation mode” and listens for 3 seconds after each LLM reply:
  - If you answer or start speaking within this window, no new wake word is required and the LLM keeps the chat history.
  - If you stay silent for three seconds after the last LLM response, the queue resets and a wake word is needed to start a new conversation.
- Tracks the user’s face (via the camera in one of the eyeballs) and steers the servos so the robot eyes/face follow the user.
- Tracks the system state (thinking, talking, cancelling, idle) and drives the robot animations accordingly.
- Optionally mirrors the system state on an RGB LED.

### Demo mode (silent showcase)

Set the environment variable `DEMOMODE=1` to skip wakeword detection, recording, network traffic (STT/LLM/TTS/MQTT), and startup dependency checks. In this mode Coglet only runs face tracking, automatic blinking via the eyelid controller, and a periodic “thinking” animation (5–10 seconds every 60 seconds) that tilts the head while alternating the ear positions. The log file emits `Attention: Demomode active` at startup to make the mode explicit.

### Piper TTS on the Pi (MQTT-based)

Piper is not started directly by `coglet-pi.py` but via a separate MQTT bridge:

- `piper_mqtt_tts.py` runs as a systemd service (`piper_mqtt_tts.service`) and:
  - subscribes to `coglet/tts/say` and `coglet/tts/cancel`
  - calls the Piper CLI (`PIPER_BIN`, `PIPER_MODEL`, `PIPER_CFG`) and writes WAV files to `/run/piper/out`
  - publishes state updates (e.g. `READY`, `START`, `SPEAKING`, `DONE`, `CANCELLED`, `ERROR`) to `coglet/tts/status`
- The `say` helper script sends `{"id": "...", "text": "..."}` JSON payloads to `coglet/tts/say` and stores the last ID in `/run/piper/last_id`.
- The `say-cancel` script sends the ID (explicit or last ID) to `coglet/tts/cancel`.

By default, Coglet is configured to use the Piper voice `en_US-lessac-high`.
You can change this by setting the `PIPER_VOICE` environment variable, e.g.:
```
PIPER_VOICE=en_GB-cori-high
PIPER_VOICE=en_US-ryan-high
PIPER_VOICE=de_DE-thorsten-high
```

`coglet-pi.py` uses MQTT-based TTS by default (`TTS_MODE=mqtt`) and falls back to a local FIFO or one-shot Piper+aplay pipeline if MQTT is not available.

## Servo layout (PCA9685)

| Name | Function | Channel |
| --- | --- | --- |
| `EYL` | Left eye | 0 |
| `EYR` | Right eye | 1 |
| `LID` | Eyelid/blinking | 2 |
| `NPT` | Head forward/backward (pitch) | 3 |
| `NRL` | Head left/right (roll) | 4 |
| `MOU` | Mouth open/close | 5 |
| `EAL` | Left ear | 6 |
| `EAR` | Right ear | 7 |
| `LWH` | Left wheel (turning in place) | 8 |
| `RWH` | Right wheel (turning in place) | 9 |

The wheels (`LWH`/`RWH`) rotate the robot in place when eye movement alone is insufficient to keep the user in view.

### Configure face tracking and wheels

The face-tracking pipeline automatically binds eye, head, and wheel servos to the PCA9685 channels from `SERVO_LAYOUT_V1`. The following environment variables allow fine-tuning:

| Variable | Description |
| --- | --- |
| `FACE_TRACKING_WHEEL_CHANNELS` | Comma-separated channels used by default for left/right wheels (default: `8,9`). |
| `FACE_TRACKING_WHEEL_LEFT_CHANNEL` / `FACE_TRACKING_WHEEL_RIGHT_CHANNEL` | Force a specific channel for the respective wheel. |
| `FACE_TRACKING_WHEEL_DEADZONE_DEG` | Eye-angle offset (from neutral) at which the wheels start moving. |
| `FACE_TRACKING_WHEEL_FOLLOW_DELAY_S` | Delay in seconds before wheels react to sustained eye deviation. |
| `FACE_TRACKING_WHEEL_INPUT_MIN_DEG` / `FACE_TRACKING_WHEEL_INPUT_MAX_DEG` | Expected input angles (typically eye angles 30–150°) for the mapping function. |
| `FACE_TRACKING_WHEEL_OUTPUT_MIN_DEG` / `FACE_TRACKING_WHEEL_OUTPUT_MAX_DEG` | Target angle range for the wheels. |
| `FACE_TRACKING_WHEEL_POWER` | Shape of the nonlinear mapping curve (2 = stronger acceleration near the edges). |

If no wheel servos are connected, leave the channel variables empty—the code will only bind eyes and head.

To disable the wheels entirely, set `FACE_TRACKING_WHEEL_CHANNELS` (and optionally the left/right variants) to an empty string; this prevents PCA9685 channel allocation and the face-tracking loop leaves the wheels untouched.

## PCA9685 servo calibration standalone

For precise servo min/max and start positions, use the CLI `hardware/pca9685_servo_calibration.py`. It runs independently of the rest of the Coglet code, drives each servo channel in sequence, and lets you determine min/max/start/stop values and store them in a json-formatted calibration file which is used by Coglet code as defaults.

### Usage

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python -m hardware.pca9685_servo_calibration --channels 0-9 --output /opt/coglet-pi/servo_calibration.json
```

Controls during calibration:

| Key | Action |
| --- | --- |
| `<` / `>` | Move to stored min / stored max angle |
| `c` | Move to hardware neutral angle |
| `+` / `-` | Increase / decrease step size in 1° increments (1–10°) |
| `u` / `d` | Increase / decrease current angle by the selected step |
| `U` / `D` | Store current angle as max / min |
| `A` / `Z` | Store current angle as start-position (A) / as stop-position (Z) |
| `x` | Reset the current channel to its factory defaults |
| `n` / `p` | Jump to next / previous servo channel |
| `Q` | Save calibration and quit |

All values are printed at the end and saved as a JSON file (default `servo_calibration.json`). The file contains `min_deg`, `max_deg`, and `start_deg` per channel and can be used directly for initialization in the Coglet code.

## Grove Vision AI standalone test

For a quick hardware test of the Grove Vision AI board (Sensecraft Face Detection model), use the CLI tool `hardware/grove_vision_ai_standalone.py`. It only requires pyserial and USB access—no servo or MQTT components.

### Prerequisites

- Vision board connected via USB (default device `/dev/ttyACM0`).
- User is in the `dialout` group or runs the tool via `sudo` to access the serial device.

### Usage

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python -m hardware.grove_vision_ai_standalone --serial-port /dev/ttyACM0 --hz 2.0
```

Relevant arguments:

| Flag | Description |
| --- | --- |
| `--serial-port` | Board serial device (default `/dev/ttyACM0`). |
| `--baud-rate` | Baud rate, default 921600. |
| `--hz` | Inference cycles per second (default 2 Hz, useful range 1–3 Hz). |
| `--timeout` | Timeout per inference run in seconds (default 0.4). |
| `--json` | Output raw data as JSON, ideal for log files. |
| `--max-iterations` | Stop automatically after n iterations (0 = infinite). |

On successful inference you will see lines like:

```
[2024-05-01 12:00:00] 1 Detections
  #1: score=0.876 x=12.0 y=43.5 w=80.0 h=80.0 cx=52.0 cy=83.5
```

If a timeout occurs or no results are returned, the tool reports `Keine Antwort (Timeout)` or `Keine Detections vom Board erhalten.`—helpful to quickly verify whether the board is responding.

### Configure ALSA to USB

/etc/asound.conf:

```bash
defaults.pcm.card 1
defaults.ctl.card 1
```
