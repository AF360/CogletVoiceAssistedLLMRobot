# Raspberry Pi side setup & code

## Operating modes

Coglet has two deliberately separate launchers:

| Mode | Launcher | Behavior |
|---|---|---|
| **Local Mode** | `coglet-local.py` | Hardware-VAD or optional wakeword-triggered local STT/LLM/TTS pipeline through the GPU server |
| **Cloud Mode** | `coglet-cloud.py` | Continuous OpenAI Realtime speech-to-speech session without wakeword |

`coglet-local.py` is local-only. It no longer contains an OpenAI Realtime execution path. To use Cloud Mode, start `coglet-cloud.py` explicitly.

The launcher selects the mode.

## Files

- `coglet-local.py` — Dedicated Local Mode launcher
- `local_mode.py` — Local hardware-VAD/wakeword STT/LLM/TTS conversation implementation
- `coglet-cloud.py` — Dedicated continuous OpenAI Realtime launcher
- `robot_runtime.py` — Public shared robot-hardware facade
- `hardware/robot_runtime.py` — Servo, LED, animation and face-tracking runtime
- `voice_backends/openai_realtime.py` — Realtime WebSocket, audio streaming, VAD event and playback implementation
- `prompts/openai-realtime-coglet-de.txt` — German Realtime persona/system prompt
- `env-exports.sh.example` — Tracked template containing the shared Local and Cloud Mode configuration
- `env-exports.sh` — Private local configuration sourced by both launchers; excluded from Git
- `email_sender.py` — Local SMTP delivery with HTML and plain-text MIME alternatives
- `piper_mqtt_tts.py` — Piper text-to-speech bridge for short local acknowledgement phrases
- `piper_mqtt_tts.service` — systemd service definition for the Piper MQTT bridge
- `piper_mqtt_tts` — Example `/etc/default/piper_mqtt_tts` environment file
- `say` — Sends text to the Piper MQTT bridge; typically installed as `/usr/local/bin/say`
- `say-cancel` — Cancels Piper speech through MQTT
- `requirements.txt` — Python dependencies
- `hardware/` — Raspberry Pi audio, servo, status LED, face tracking and calibration modules
- `README.md` — This file

## Installation path

The examples below assume:

```text
/opt/coglet-pi
```

Create the private shared environment file once and activate the virtual environment:

```bash
cd /opt/coglet-pi
cp env-exports.sh.example env-exports.sh
vi env-exports.sh
source .venv/bin/activate
```

Both launchers source the same `env-exports.sh`. The selected executable determines the operating mode.

## Local Mode

Start manually with:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-local.py
```

The production installation may start `coglet-local.py` through its systemd service instead.

### Local conversation path

```text
-> XVF3800 hardware VAD speech trigger or optional OpenWakeWord detection
-> short local Piper acknowledgement when OpenWakeWord mode is used
-> microphone recording until 700ms silence
-> Faster-Whisper STT on the GPU server
-> Ollama LLM on the GPU server
-> Piper/MQTT TTS on the Raspberry Pi
-> local follow-up conversation window
```

Local Mode:

- waits for the XVF3800 hardware VAD speech trigger by default, or for OpenWakeWord when configured;
- plays the configured short acknowledgement, such as “Ja?”, only when OpenWakeWord mode is used;
- records speech until WebRTC VAD detects the end of the utterance or silence for 700ms;
- performs early local command and email-request detection after STT;
- calls the configured Ollama model directly for normal conversation requests;
- speaks the generated reply through the local Piper/MQTT bridge;
- keeps local conversation history during follow-up turns;
- returns to the speech/wake trigger wait state after follow-up silence;
- controls face tracking, servos, animations, status LED and deep sleep;
- can generate and send requested emails through the local Ollama and SMTP path.

Local Mode does not require `OPENAI_API_KEY` and does not send conversation audio to OpenAI.

### Local email behavior

The Local Mode email path:

1. detects an explicit email request after local STT;
2. asks the configured local Ollama model to create a subject and HTML body;
3. sends the email through `email_sender.py` and the locally configured SMTP server;
4. speaks a local confirmation and returns to the speech/wake trigger wait state.

### Demo mode

Set:

```bash
export DEMOMODE=1
```

Demo Mode skips speech/wake trigger detection, recording, STT/LLM/TTS network traffic, MQTT dependency checks and normal conversation. Coglet only runs face tracking, automatic blinking and periodic animations. The log contains `Attention: Demomode active` at startup.

## OpenAI Realtime Cloud Mode

Start manually with:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-cloud.py
```

Store the real API key, SMTP credentials and local hardware overrides only in the private `env-exports.sh`; do not commit that file.

`coglet-cloud.py` always starts OpenAI Realtime. It has no Local Mode path and does not fall back to `coglet-local.py`.

### Cloud conversation path

```text
Program start
    -> immediate continuous OpenAI Realtime session
    -> server-side VAD detects turns
    -> OpenAI speech recognition, reasoning and speech generation
    -> streamed PCM playback on the Raspberry Pi
    -> session remains open until shutdown
```

Cloud Mode has no wakeword and does not use the local Faster-Whisper, Ollama or Piper conversation pipeline.

The Raspberry Pi still handles:

- ReSpeaker microphone and speaker I/O;
- hardware AEC/VAD/DoA integration where available;
- face tracking, status LED and robot animations;
- servo initialization and parking;
- local SMTP delivery for email tool calls;
- graceful program shutdown.

### Realtime configuration

Typical variables in the shared `env-exports.sh`:

```bash
export OPENAI_API_KEY=""
export OPENAI_REALTIME_MODEL="gpt-realtime-2"
export OPENAI_REALTIME_VOICE="marin"
export OPENAI_REALTIME_REASONING_EFFORT="low"
export OPENAI_REALTIME_VAD_MODE="server_vad"
export OPENAI_REALTIME_TRANSCRIPTION="true"
export OPENAI_REALTIME_TRANSCRIPTION_MODEL="gpt-4o-mini-transcribe"
export OPENAI_REALTIME_STARTUP_MESSAGE="Ich bin online und bereit zu helfen."
export OPENAI_REALTIME_EXIT_PHRASES="programm ende,programmende,programm-ende,coglet shutdown,coglet ausschalten,coglet beenden"
export OPENAI_REALTIME_SHUTDOWN_MESSAGE="Tschüss!"
export OPENAI_REALTIME_LOCAL_BARGE_IN="true"
export OPENAI_REALTIME_BARGE_IN_MIN_DBFS="-35"
export OPENAI_REALTIME_INSTRUCTIONS_FILE="/opt/coglet-pi/prompts/openai-realtime-coglet-de.txt"
```

The Cloud launcher validates `OPENAI_API_KEY`, the `websocket-client` dependency and the configured Realtime VAD mode before opening the session. A missing or invalid requirement stops Cloud Mode with an error; it never switches to Local Mode.

Cloud Mode also runs a local barge-in detector while assistant audio is playing. When local speech is detected, Coglet stops the local `aplay` output immediately and cancels the active Realtime response. Tune `OPENAI_REALTIME_BARGE_IN_MIN_DBFS`, `OPENAI_REALTIME_BARGE_IN_FRAMES` and `OPENAI_REALTIME_BARGE_IN_VAD_AGGRESSIVENESS` if the ReSpeaker/AEC setup is too sensitive or not sensitive enough.

### Realtime email tool

Cloud Mode registers the `send_email` function tool with OpenAI Realtime.

When the user explicitly asks to send information by email:

1. the Realtime model creates a subject and structured HTML body;
2. the model calls `send_email(subject, body)`;
3. Coglet reads the fixed recipient from local `EMAIL_TO` configuration;
4. the Pi sends the message through the existing local SMTP implementation;
5. the tool result is returned to Realtime;
6. the Realtime voice confirms success or reports the error.

The recipient and SMTP credentials remain local and are not model-controlled.

### Shutdown behavior

`Ctrl+C`, `SIGTERM` or a configured spoken shutdown command triggers a graceful shutdown:

1. face tracking stops;
2. the active Realtime response is cancelled or allowed to finish safely;
3. Coglet speaks the configured shutdown message through the Realtime voice;
4. the WebSocket session closes;
5. session usage is logged;
6. the servos move to their calibrated park positions.

A second shutdown signal forces immediate termination.

### Session usage statistics

At session close, Cloud Mode logs aggregated usage from all `response.done` events:

```text
[openai-realtime] Session usage:
  Responses:                  21
  Input tokens:            32450
    Text:                  25434
    Audio:                  7016
    Cached:                25792
  Output tokens:            3010
    Text:                   1666
    Audio:                  1344
  Total tokens:            35460
  Session duration:     00:02:10
```

Input-token totals represent the API usage across responses and may include conversation context again on later turns. Cached tokens are shown separately.

Cloud Mode sends conversation audio to OpenAI and incurs API costs.

## Piper TTS on the Pi

Piper is not started directly by `coglet-local.py`. The separate MQTT bridge:

- subscribes to `coglet/tts/say` and `coglet/tts/cancel`;
- invokes the Piper CLI;
- writes temporary WAV files under `/run/piper/out`;
- publishes `READY`, `START`, `SPEAKING`, `DONE`, `CANCELLED` and `ERROR` states;
- supports cancellation through the `say-cancel` helper.

Piper is used for Local Mode prompts, acknowledgements and main conversation replies. Cloud Mode replies use the selected OpenAI Realtime voice.

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

The face-tracking pipeline automatically binds eye, head and wheel servos to the PCA9685 channels from `SERVO_LAYOUT_V1`.

| Variable | Description |
| --- | --- |
| `FACE_TRACKING_WHEEL_CHANNELS` | Comma-separated default channels for left/right wheels (`8,9`). |
| `FACE_TRACKING_WHEEL_LEFT_CHANNEL` / `FACE_TRACKING_WHEEL_RIGHT_CHANNEL` | Force a channel for the respective wheel. |
| `FACE_TRACKING_WHEEL_DEADZONE_DEG` | Eye-angle offset at which wheel movement starts. |
| `FACE_TRACKING_WHEEL_FOLLOW_DELAY_S` | Delay before wheels react to sustained eye deviation. |
| `FACE_TRACKING_WHEEL_INPUT_MIN_DEG` / `FACE_TRACKING_WHEEL_INPUT_MAX_DEG` | Expected eye-angle input range. |
| `FACE_TRACKING_WHEEL_OUTPUT_MIN_DEG` / `FACE_TRACKING_WHEEL_OUTPUT_MAX_DEG` | Target wheel angle range. |
| `FACE_TRACKING_WHEEL_POWER` | Shape of the nonlinear mapping curve. |

Leave the wheel-channel variables empty when no wheel servos are connected. To disable wheels entirely, set `FACE_TRACKING_WHEEL_CHANNELS` and optional left/right overrides to empty strings.

## PCA9685 servo calibration standalone

Use:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python3 -m hardware.pca9685_servo_calibration \
  --channels 0-9 \
  --output /opt/coglet-pi/servo_calibration.json
```

| Key | Action |
| --- | --- |
| `<` / `>` | Move to stored minimum / maximum angle |
| `c` | Move to hardware neutral angle |
| `+` / `-` | Change step size |
| `u` / `d` | Move by the selected step |
| `U` / `D` | Store current angle as maximum / minimum |
| `x` | Reset current channel to factory defaults |
| `n` / `p` | Next / previous servo channel |
| `Q` | Save calibration and quit |

The JSON calibration contains `min_deg`, `max_deg`, `start_deg` and, where configured, calibrated stop/park values.

## Grove Vision AI standalone test

Prerequisites:

- Grove Vision AI board connected through USB, normally `/dev/ttyACM0`;
- permission to access the serial device, normally through the `dialout` group.

Run:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python3 -m hardware.grove_vision_ai_standalone \
  --serial-port /dev/ttyACM0 \
  --hz 2.0
```

| Flag | Description |
| --- | --- |
| `--serial-port` | Serial device, default `/dev/ttyACM0` |
| `--baud-rate` | Baud rate, default `921600` |
| `--hz` | Inference cycles per second |
| `--timeout` | Timeout per inference run |
| `--json` | Emit raw JSON |
| `--max-iterations` | Stop after n iterations; `0` means unlimited |

Successful output contains detected faces with confidence and bounding-box coordinates. Timeouts or empty results are reported explicitly for hardware diagnosis.

## Shared robot runtime

`coglet-local.py` and `coglet-cloud.py` share physical robot hardware through `robot_runtime.py`; command normalization lives in `command_utils.py`. The Cloud launcher no longer loads or executes `coglet-local.py`.

## Language selection

Set `COGLET_LANG=de` or `COGLET_LANG=en` in `env-exports.sh`. Local Mode uses this for STT/TTS/prompt defaults. Cloud Mode uses the same switch for OpenAI Realtime prompt-file selection and localized cloud helper texts. To bypass the language defaults for Cloud Mode, set `OPENAI_REALTIME_INSTRUCTIONS` or `OPENAI_REALTIME_INSTRUCTIONS_FILE`.
