# Coglet – Technical Overview

This document gives a technical, implementation-level overview of the **Coglet** stack as it exists in this repository.  
It complements the high-level hybrid diagram in `SYSTEM-ARCHITECTURE.md`.

## 1. High-level architecture

Coglet is a **robot / animatronic prototype** with a voice I/O interface to a **local LLM** in a client–server architecture
for natural-language interaction (`README.md`, lines 1–2).

- The **Raspberry Pi 4/5** runs the real-time parts:
  - Wake-word detection (openWakeWord)
  - Microphone capture and VAD
  - Conversation state machine and orchestration
  - Face tracking (Grove Vision AI v2)
  - Servo control (PCA9685 + MG90S servos)
  - Local TTS playback via Piper and a USB audio device
  - Optional RGB status LED (WS281x NeoPixel)

  (See `SYSTEM-ARCHITECTURE.md`, lines 3–4 and 8–16; `pi-side/README.md`, lines 3–13 and 31–33; and `README.md`, lines 8–16.)

- A **Linux PC/Mac with NVIDIA GPU** hosts:
  - The Faster-Whisper STT HTTP server (`/stt`)
  - The Ollama daemon with the custom **Coglet** chat model based on Qwen2.5:7b-instruct (or similar)

  (See `SYSTEM-ARCHITECTURE.md`, lines 10–14; `README.md`, lines 17–23; and `server-side/INSTALLATION.md`, lines 1–4.)

All data is processed **locally without any cloud dependency** (`SYSTEM-ARCHITECTURE.md`, line 71).

## 2. Hardware overview

The current hardware bill of materials is documented in `README.md`, lines 6–17 and includes:

- Raspberry Pi 5 (8 GB)
- Grove Vision AI v2 camera module
- Seeedstudio ReSpeaker USB 4-Mic-Array (XVF3800) audio module with four digital far-field microphones
- Passive 4 Ω / 3–5 W speaker on the ReSpeaker amplifier output (AEC requirement)
- 10 × MG90S micro servos driven by a PCA9685 board
- Mean Well RSP-100-5 5 V / 20 A PSU for servos
- Single WS281x RGB LED (5 mm NeoPixel) with a 3.3 V → 5 V level shifter
- 3D-printed mechanical parts for Coglet
- A Linux PC with NVIDIA RTX GPU (≥ 8–12 GB VRAM recommended)

## 3. Raspberry Pi side

### 3.1 Main orchestrator (`coglet-pi.py`)

The Pi-side entrypoint is `pi-side/coglet-pi.py`.  
Its top-level docstring summarises the main data-flow (`pi-side/coglet-pi.py`, lines 3–7):

- Wake-word (`openwakeword`) → recording via `sounddevice` + `webrtcvad` → STT HTTP `/stt` on the PC
- LLM chat via Ollama `/api/chat` with streaming
- TTS via Piper → audio playback
- Half-duplex behaviour: the microphone is muted while TTS is running.

Environment variables for this process are listed in the same docstring (`pi-side/coglet-pi.py`, lines 9–16), including:

- STT and LLM: `STT_URL`, `OLLAMA_URL`, `OLLAMA_MODEL`, `LLM_KEEP_ALIVE`
- Audio input: `MIC_SR`, `MIC_DEVICE`
- Wake-word: `WAKEWORD_BACKEND`, `OWW_MODEL`, `OWW_THRESHOLD`
- TTS: `PIPER_VOICE`, `PIPER_VOICE_JSON`, `PIPER_FIFO`, `TTS_WPM`, `TTS_PUNCT_PAUSE_MS`

In addition to the docstring, `coglet-pi.py` defines further TTS-related configuration such as `TTS_MODE` and MQTT settings
for Piper (`pi-side/coglet-pi.py`, lines 119–149).

### 3.2 Wake-word and recording pipeline

Wake-word detection is implemented around the `Wakeword` class in `coglet-pi.py` (`pi-side/coglet-pi.py`, lines 1520–1529):

- The code uses **openWakeWord** and expects input as mono `float32` audio which is resampled to 16 kHz and converted to
  `int16` for the model.
- Window and hop sizes are configured in milliseconds but internally snapped to multiples of 1280 samples
  (80 ms at 16 kHz) (`pi-side/coglet-pi.py`, lines 1525–1527 and 1552–1556).
- Environment variables such as `OWW_WIN_MS`, `OWW_HOP_MS`, `WAKE_REARM_RATIO`, `WAKE_MIN_GAP_S` and
  `WAKE_REARM_LOW_COUNT` control wake-word sensitivity and re-arm behaviour (`pi-side/coglet-pi.py`, lines 1552–1561).

Recording uses `sounddevice` and WebRTC VAD as described in the `pi-side/README.md` bullet list for `coglet-pi.py`
(`pi-side/README.md`, lines 22–31): after the wake-word confirmation sound, recording starts and runs until VAD detects
silence; then the WAV file is sent to the STT server on port 5005 and the recognised text is forwarded to the LLM.

### 3.3 STT and LLM requests

The main loop sends audio and text to the back-end services according to:

- `pi-side/README.md`, lines 25–27: STT via HTTP POST to the Faster-Whisper server on port 5005 and LLM requests to the
  Ollama server on port 11434.
- `SYSTEM-ARCHITECTURE.md`, lines 10–14, which explicitly list the STT HTTP server and Ollama daemon on the PC.

The **follow-up conversation mode** is documented in `pi-side/README.md`, lines 28–30: after each LLM reply Coglet listens
for approx. three seconds; if the user replies within this window, the conversation continues without a new wake-word,
otherwise the chat history is reset.

### 3.4 Piper TTS integration

TTS is driven by the `say()` helper in `coglet-pi.py` (`pi-side/coglet-pi.py`, lines 1148–1188):

- Text is wrapped into a small JSON payload and processed inside a `half_duplex_tts()` context, which enforces that the
  microphone is muted during playback.
- **Primary path:** when `TTS_MODE` is set to `"mqtt"` and MQTT support is available, text is sent to the Piper service
  via MQTT using `_piper_mqtt_publish()`; Coglet then waits for TTS lifecycle status messages to drive the mouth
  animation and timing.
- **Fallback path:** if MQTT is not available, `say()` falls back to writing JSON lines into `PIPER_FIFO` and, as a last
  resort, to a one-shot Piper subprocess with `aplay` output (`pi-side/coglet-pi.py`, lines 1190–1215).

The user-facing `say` helper script publishes the same JSON payload to an MQTT topic (`pi-side/say`, lines 4–8 and
22–25).

### 3.5 Status LED

The RGB status LED is implemented in `pi-side/hardware/status_led.py`:

- The `CogletState` enum defines LED-relevant states such as `AWAIT_FOLLOWUP`, `LISTENING`, `THINKING`, `SPEAKING` and
  `OFF` (`pi-side/hardware/status_led.py`, lines 22–28).
- `StatusLED` drives a single NeoPixel via `neopixel.NeoPixel` on GPIO 21 by default and honours the `ENABLE_LED`
  environment variable; when the LED is disabled, all calls are effectively no-ops (`pi-side/hardware/status_led.py`,
  lines 31–75 and 78–90).
- The mapping of Coglet states to colours is defined in `set_state()`, e.g. violet for `AWAIT_FOLLOWUP`, red for
  `LISTENING`, blue for `THINKING`, and green for `SPEAKING` (`pi-side/hardware/status_led.py`, lines 94–121).

### 3.6 Face tracking and servos

Face tracking is encapsulated in `pi-side/hardware/face_tracker.py`:

- The module is documented as a *“High-level face-tracking controller for Grove Vision AI results”*
  (`pi-side/hardware/face_tracker.py`, line 1).
- It depends on `GroveVisionAIClient` and `FaceDetectionBox` from `grove_vision_ai.py` and on the generic `Servo`
  abstraction from `pca9685_servo.py` (`pi-side/hardware/face_tracker.py`, lines 11–13).

Servo handling is built on `pi-side/hardware/pca9685_servo.py` and the preset layout in
`pi-side/hardware/servo_presets.py`:

- `ServoConfig` describes servo limits, speed and acceleration constraints (`pi-side/hardware/pca9685_servo.py`,
  lines 37–47).
- `Servo.update()` applies acceleration and speed limits per tick to move the servo towards its target angle
  (`pi-side/hardware/pca9685_servo.py`, lines 130–160).
- `SERVO_LAYOUT_V1` and multiple named poses such as `POSE_REST` and `POSE_THINKING_1` are provided by
  `servo_presets.py` (`pi-side/hardware/servo_presets.py`, lines 10–16).

An autonomous blinking controller for the eyelid servo is implemented in `pi-side/hardware/eyelid_controller.py`, which
contains the `EyelidController` class and describes itself as a *“Threaded controller to drive an eyelid servo with
autonomous blinking”* (`pi-side/hardware/eyelid_controller.py`, lines 13–16). It exposes a small mode API
(`auto`, `hold`, `closed`, `sleep`) via `set_mode()` (`pi-side/hardware/eyelid_controller.py`, lines 68–79).

### 3.7 Startup dependency checks

Before the main loop starts, Coglet performs dedicated startup checks implemented in `pi-side/startup_checks.py`:

- STT health: `_check_stt_health()` calls the `/healthz` endpoint of the STT HTTP server and verifies that it reports
  `"ok": True` (`pi-side/startup_checks.py`, lines 35–48).
- LLM model availability: `_check_ollama_model()` queries the `/api/tags` endpoint of the Ollama daemon and ensures that
  the configured model is present (`pi-side/startup_checks.py`, lines 62–76).
- Piper MQTT: `_check_piper_mqtt()` verifies MQTT connectivity, configuration of the Piper host and the availability
  of `paho-mqtt` (`pi-side/startup_checks.py`, lines 107–120).

Unit tests for these checks and the hardware abstractions live in `pi-side/tests/`, e.g.
`test_startup_checks.py`, `test_face_tracker.py`, `test_pca9685_servo.py` and others.

## 4. Server side

The server side is documented in `server-side/README.md` and `server-side/INSTALLATION.md`:

- It targets **Debian 12 (“Bookworm”)** with an NVIDIA GPU (tested with ≥ 8–12 GB VRAM) (`server-side/INSTALLATION.md`,
  lines 1–4 and 10–12).
- `stt_http_server.py` implements a Flask-based STT HTTP server around Faster-Whisper with two endpoints:
  `GET /healthz` and `POST /stt` (`server-side/stt_http_server.py`, lines 3–7 and 77–80).
- Whisper configuration is controlled via environment variables such as `WHISPER_MODEL`, `WHISPER_DEVICE`,
  `WHISPER_COMPUTE` and `STT_HTTP_PORT` (`server-side/stt_http_server.py`, lines 28–32).
- A sample environment block and curl test command for `/stt` are included at the end of `server-side/README.md`
  (`server-side/README.md`, lines 71–78).

Ollama is configured to listen on `0.0.0.0:11434` via `OLLAMA_HOST`, as noted in `SYSTEM-ARCHITECTURE.md`,
lines 60–64.

The `server-side/INSTALLATION.md` guide covers:

- Base system preparation and NVIDIA driver / CUDA setup
- Installation of Ollama and creation of the `coglet` model based on **Qwen2.5:7b-instruct**
- Installation and service setup for Faster-Whisper using a dedicated `coglet` service user

(See `server-side/INSTALLATION.md`, lines 3, 109–119 and following.)

## 5. Ports and protocols

The key network-visible ports are summarised in `SYSTEM-ARCHITECTURE.md` (table around lines 32–38) and the other
documents:

- **STT**: HTTP on TCP port **5005**, `/stt`
- **LLM (Ollama)**: HTTP on TCP port **11434**, `/api/chat`
- **TTS**: MQTT on the Raspberry Pi (default localhost:1883), driven by the `say` script and `piper_mqtt_tts` service
- **Internal audio and servo control** remain local to the Pi (ALSA, I²C for PCA9685, GPIO for the WS281x LED)

Firewall configuration must at least allow inbound TCP 5005 and 11434 on the STT/LLM host so the Raspberry Pi can reach
both services (`SYSTEM-ARCHITECTURE.md`, line 40 and the note below the diagram).

---

This overview is intentionally implementation-oriented and directly references the modules and line ranges present in the
current repository ZIP so it stays in sync with the actual code.
