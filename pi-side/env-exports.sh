#!/usr/bin/env bash

### --- Endpunkte / Modelle ---
# Adjust to your IP-settings
export STT_URL="http://192.168.10.161:5005"
export STT_RESAMPLE_TO_HZ="16000"
export OLLAMA_URL="http://192.168.10.161:11434"
export OLLAMA_MODEL="coglet:latest"

# NEW: Main Language Switch (de/en)
# Affects system prompts, TTS voice, STT lang and email templates.
export COGLET_LANG="en"

# NOTE: Voice/Model configs are now handled dynamically via COGLET_LANG in python.
# Uncomment below only to override the language defaults.
# export STT_LANG="en"
# export PIPER_MODEL="/opt/piper/voices/en_US-ryan-high.onnx"
# export PIPER_CFG="/opt/piper/voices/en_US-ryan-high.onnx.json"
# export PIPER_VOICE="/opt/piper/voices/en_US-ryan-high.onnx"
# export PIPER_VOICE_JSON="/opt/piper/voices/en_US-ryan-high.onnx.json"
# NOTE: Prompt strings are also handled dynamically.
# Uncomment only to override.
# export MODEL_CONFIRM="Ja?"
# export MODEL_READY="Alle Subsysteme bereit. Ich erwarte das Wähkwörd."
# export MODEL_BYEBYE="Tschüssen!"
# export EOC_ACK="OK. Ich warte aufs neue Wähkwörd."
# export AMS_ACK="Ich warte wieder auf das Wähkwört."
# export DS_ACK="Ich mache ein Nickerchen. Weck mich mit dem Wähkwört."

export SPEAKER_DEVICE="spk"
export APLAY_FORMAT="S16_LE"
export MODEL_CONFIRM="Yes?"
export MODEL_READY="All subsystems running. Listening for the wakeword."
export MODEL_BYEBYE="See ya!"


export PIPER_FIFO="/run/piper/in.jsonl"
export PIPER_EVENTS_FIFO="/run/piper/events"
export PIPER_CTRL_FIFO="/run/piper/ctrl"
export PIPER_BIN="/opt/piper/piper"
export SPEAKER_DEVICE="spk"
export APLAY_FORMAT="S16_LE"

### --- Demo mode ---
export DEMOMODE="0"   # Skip startup checks, wakeword, audio, MQTT; only face tracking + eyelid/ear animations

### --- Microphon / Recorder ---
export MIC_DEVICE="mic"
export MIC_SR="16000"
export MIC_CHANNELS="1"
export MIC_GAIN_DB="0"
export MIC_AUTO_GAIN="0"
export MIC_TARGET_DBFS="-18"
export MIC_MAX_GAIN_DB="35"
export SPEAKER_DEVICE="spk"  # with dmix use "spk"
export BARGE_IN="0"

### --- VAD / Endpointing (WebRTC) ---
export VAD_AGGR="2"
export VAD_FRAME_MS="30"
export VAD_START_WIN="5"
export VAD_START_MIN="3"
# jetzt 250, war 400
export VAD_END_HANG_MS="250"
export VAD_PREROLL_MS="240"
export NO_SPEECH_TIMEOUT_S="3.0"
export MAX_UTTER_S="8.0"
export VAD_START_CONSEC_MIN="3"
# jetzt 600, war 1200
export VAD_END_GUARD_MS="1200"

### --- Wakeword / OpenWakeWord ---
export WAKEWORD_BACKEND="oww"
export OWW_MODEL="/opt/coglet-pi/.venv/lib/python3.13/site-packages/openwakeword/resources/models/coglet.onnx"
export OWW_THRESHOLD="0.3"
#export OWW_WIN_MS="800"
#export OWW_HOP_MS="160"
export OWW_DEBUG="0"          
export OWW_SUPPRESS_AFTER_TTS_S="0.8"
export WAKE_REARM_RATIO="0.6"
export WAKE_REARM_LOW_COUNT="3"
export WAKE_MIN_GAP_S="1.5"
# war 1.2, jetzt 0.5
export COOLDOWN_AFTER_TTS_S="0.6"   

### --- Conversation awareness settings
export FOLLOWUP_ENABLE=1            
export FOLLOWUP_ARM_S=3.0           # Zeitfenster, in dem die nächste Äußerung starten darf
export FOLLOWUP_MAX_TURNS=0         # max. Folge-Turns nach einem Wake (0 = unbegrenzt)
export FOLLOWUP_COOLDOWN_S=0.10     # ganz kurzer Cooldown vorm Follow-up-Hören
export LLM_USE_CHAT=1               # LLM context-aware
export LLM_RESET_ON_WAKE=1          # New context each time the Wakeword initiates a new sessio
export LLM_CTX_TURNS=10
# export LLM_SYSTEM_PROMPT="Du bist Coglet, ein freundlicher, faktentreuer Roboter-Assistent. Antworte knapp und präzise auf Deutsch. Verwende die Du-Form."
export LLM_TEMPERATURE=0.3
export LLM_NUM_CTX=8192             # 4096 on weaker systems
export LLM_KEEP_ALIVE="30m"

### --- Email Sender ---
export EMAIL_TO="your.recipient@example.com"
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_FROM="your.senderadress@gmail.com"
export SMTP_STARTTLS="1"
export SMTP_SSL="0"
export SMTP_USERNAME="your.senderadress@gmail.com"
export SMTP_PASSWORD="your-email-app-password"

# Piper Server Environment
LOG_LEVEL="DEBUG"
LOGLEVEL="DEBUG"

### --- Piper via MQTT ---
export TTS_MODE="mqtt"
export PIPER_MQTT_HOST="127.0.0.1"           # or your broker IP
export PIPER_MQTT_PORT="1883"
export PIPER_MQTT_TOPIC="coglet/tts"          # topic where your Piper-MQTT listens
export MQTT_BASE="coglet/tts"
export PIPER_MQTT_USERNAME=""
export PIPER_MQTT_PASSWORD=""
export PIPER_MQTT_QOS="0"
export PIPER_MQTT_TLS="0"

### --- Status LED ---
export ENABLE_LED="1"

### Timeout for enterng deep sleep
export DEEP_SLEEP_TIMEOUT_S="300.0"

### --- Body Orientation / Turn-to-Voice ---
export TURN_TO_VOICE="1"       # 1 = turn body towards speaker when wakeword detected
export DOA_OFFSET="0"          # mounting offset in degrees
export TURN_SPEED="40.0"       # Wheel speed (0..100)
export TURN_SEC_PER_DEG="0.015" # Duration to turn 1 degree

### --- Face Tracking (Grove Vision AI + PCA9685-Servos) ---
# Master switch
export FACE_TRACKING_ENABLED="1"
export FACE_TRACKING_PATROL_ENABLED="1"
export FACE_TRACKING_PATROL_INTERVAL_S="30.0"

# Grove Vision AI UART
export FACE_TRACKING_SERIAL_PORT="/dev/ttyACM0"
export FACE_TRACKING_BAUDRATE="921600"
export FACE_TRACKING_SERIAL_TIMEOUT="0.0"

# PCA9685 basics
export FACE_TRACKING_PWM_FREQ_HZ="50.0"

# Channel mapping (defaults follow SERVO_LAYOUT_V1; wheels can be disabled with an empty list)
export FACE_TRACKING_EYE_CHANNELS="0,1"           # linkes/rechtes Auge (EYL/EYR)
export FACE_TRACKING_WHEEL_CHANNELS="8,9"         # linkes/rechtes Rad (LWH/RWH); leer = Wheels aus
export FACE_TRACKING_EYE_LEFT_CHANNEL="0"          # setzt erstes Auge explizit
export FACE_TRACKING_EYE_RIGHT_CHANNEL="1"         # setzt zweites Auge explizit
export FACE_TRACKING_YAW_CHANNEL=""              # Kopf links/rechts (NRL)
export FACE_TRACKING_PITCH_CHANNEL="3"            # Kopf hoch/runter (NPT)
export FACE_TRACKING_WHEEL_LEFT_CHANNEL="8"        # überschreibt erstes Rad
export FACE_TRACKING_WHEEL_RIGHT_CHANNEL="9"       # überschreibt zweites Rad

# Geometrie & Controller-Gains (siehe hardware/face_tracker.FaceTrackingConfig)
# Vorher: 220.0 / 200.0- Alternativ zu 192/192 auch 240/240
export FACE_TRACKING_FRAME_WIDTH="320.0"
export FACE_TRACKING_FRAME_HEIGHT="320.0"
#export FACE_TRACKING_FRAME_WIDTH="220.0"
#export FACE_TRACKING_FRAME_HEIGHT="200.0"
export FACE_TRACKING_COORDINATES_CENTER="1"
export FACE_TRACKING_EYE_DEADZONE_PX="10.0"
export FACE_TRACKING_PITCH_DEADZONE_PX="18.0"
export FACE_TRACKING_EYE_GAIN_DEG_PER_PX="0.08"
export FACE_TRACKING_PITCH_GAIN_DEG_PER_PX="-0.06"
# Nickrichtung beim FT falsch herum, daher geändertes Vorzeichen
# export FACE_TRACKING_PITCH_GAIN_DEG_PER_PX="0.06"
export FACE_TRACKING_EYE_MAX_DELTA_DEG="20.0"
export FACE_TRACKING_PITCH_MAX_DELTA_DEG="20.0"
# FT-Invoke 0.05 = 20 fps SeedVision 
export FACE_TRACKING_INVOKE_INTERVAL_S="0.05"
export FACE_TRACKING_INVOKE_TIMEOUT_S="0.5"
# FT-Servoupdate: 0.02 = 50 Hz, 0.01 = 100 Hz
export FACE_TRACKING_UPDATE_INTERVAL_S="0.01"
export FACE_TRACKING_NEUTRAL_TIMEOUT_S="2.0"
export FACE_TRACKING_WHEEL_DEADZONE_DEG="5.0"
export FACE_TRACKING_WHEEL_FOLLOW_DELAY_S="0.8"
export FACE_TRACKING_WHEEL_INPUT_MIN_DEG="30.0"
export FACE_TRACKING_WHEEL_INPUT_MAX_DEG="150.0"
export FACE_TRACKING_WHEEL_OUTPUT_MIN_DEG="80.0"
export FACE_TRACKING_WHEEL_OUTPUT_MAX_DEG="100.0"
export FACE_TRACKING_WHEEL_POWER="2.0"

# Servo-Tuning (optional Overrides; Basis sind SERVO_LAYOUT_V1-Presets)
#export FACE_TRACKING_EYE_MIN_ANGLE_DEG="30.0"
#export FACE_TRACKING_EYE_MAX_ANGLE_DEG="150.0"
export FACE_TRACKING_EYE_MIN_PULSE_US="600.0"
export FACE_TRACKING_EYE_MAX_PULSE_US="2400.0"
#export FACE_TRACKING_EYE_MAX_SPEED_DEG_PER_S="200.0"   # rechter Servo Preset: 250.0
#export FACE_TRACKING_EYE_MAX_ACCEL_DEG_PER_S2="1000.0"
#export FACE_TRACKING_EYE_DEADZONE_DEG="0.8"
# export FACE_TRACKING_EYE_NEUTRAL_DEG="90.0"
#export FACE_TRACKING_EYE_INVERT="0"

#export FACE_TRACKING_PITCH_MIN_ANGLE_DEG="1.0"
#export FACE_TRACKING_PITCH_MAX_ANGLE_DEG="120.0"
export FACE_TRACKING_PITCH_MIN_PULSE_US="600.0"
export FACE_TRACKING_PITCH_MAX_PULSE_US="2400.0"
#export FACE_TRACKING_PITCH_MAX_SPEED_DEG_PER_S="600.0"
#export FACE_TRACKING_PITCH_MAX_ACCEL_DEG_PER_S2="400.0"
#export FACE_TRACKING_PITCH_DEADZONE_DEG="1.0"
# export FACE_TRACKING_PITCH_NEUTRAL_DEG="50.0"
#export FACE_TRACKING_PITCH_INVERT="0"

#export FACE_TRACKING_WHEEL_LEFT_MIN_ANGLE_DEG="30.0"
#export FACE_TRACKING_WHEEL_LEFT_MAX_ANGLE_DEG="120.0"
export FACE_TRACKING_WHEEL_LEFT_MIN_PULSE_US="600.0"
export FACE_TRACKING_WHEEL_LEFT_MAX_PULSE_US="2400.0"
#export FACE_TRACKING_WHEEL_LEFT_MAX_SPEED_DEG_PER_S="100.0"
#export FACE_TRACKING_WHEEL_LEFT_MAX_ACCEL_DEG_PER_S2="25.0"
#export FACE_TRACKING_WHEEL_LEFT_DEADZONE_DEG="1.0"
## export FACE_TRACKING_WHEEL_LEFT_NEUTRAL_DEG="90.0"
export FACE_TRACKING_WHEEL_LEFT_INVERT="1"

#export FACE_TRACKING_WHEEL_RIGHT_MIN_ANGLE_DEG="30.0"
#export FACE_TRACKING_WHEEL_RIGHT_MAX_ANGLE_DEG="120.0"
export FACE_TRACKING_WHEEL_RIGHT_MIN_PULSE_US="600.0"
export FACE_TRACKING_WHEEL_RIGHT_MAX_PULSE_US="2400.0"
#export FACE_TRACKING_WHEEL_RIGHT_MAX_SPEED_DEG_PER_S="100.0"
#export FACE_TRACKING_WHEEL_RIGHT_MAX_ACCEL_DEG_PER_S2="25.0"
#export FACE_TRACKING_WHEEL_RIGHT_DEADZONE_DEG="1.0"
# export FACE_TRACKING_WHEEL_RIGHT_NEUTRAL_DEG="90.0"
export FACE_TRACKING_WHEEL_RIGHT_INVERT="1"
