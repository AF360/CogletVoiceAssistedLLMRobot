# Coglet – Technical Overview

This document describes the current implementation-level stack in this repository.

## 1. High-level architecture

Coglet has two deliberately separate launchers:

| Mode | Launcher | Pipeline |
|---|---|---|
| Local Mode | `pi-side/coglet-local.py` | Hardware VAD or optional wakeword → recording → Faster-Whisper STT → Ollama → Piper/MQTT TTS |
| Cloud Mode | `pi-side/coglet-cloud.py` | Continuous OpenAI Realtime speech-to-speech session |

`coglet-local.py` imports `local_mode.py` and provides the local hardware-VAD/wakeword STT/LLM/TTS mode. `coglet-cloud.py` starts OpenAI Realtime directly and does not use the local conversation pipeline.

## 2. Raspberry Pi side

The Raspberry Pi owns the real-time robot runtime:

- XVF3800 hardware VAD wake trigger or optional OpenWakeWord detection
- microphone capture and WebRTC VAD
- local conversation state and follow-up handling
- Piper/MQTT speech output
- ReSpeaker hardware integration
- face tracking and servo control through the shared robot runtime
- optional RGB status LED
- deep sleep and wake-up behavior

## 3. Local Mode conversation path

```text
Hardware VAD speech trigger or optional wakeword
  -> optional MODEL_CONFIRM through Piper when wakeword mode is used
  -> record user audio
  -> POST WAV to STT_URL /stt
  -> local command/email detection
  -> Ollama /api/chat or /api/generate
  -> speak reply through Piper/MQTT
  -> follow-up window
```

Conversation memory is local in `ConversationMemory` inside `local_mode.py`. On a new wake/speech-triggered session, `LLM_RESET_ON_WAKE=1` clears that local memory.

## 4. Server side

`server-side/stt_http_server.py` provides the Faster-Whisper `/stt` and `/healthz` endpoints. Ollama runs as a normal service and is called directly from Local Mode through `OLLAMA_URL`.

The server side no longer contains a combined STT/LLM/TTS pipeline. Speech synthesis is handled by Piper on the Raspberry Pi side.

## 5. Cloud Mode

The optional cloud launcher uses `pi-side/voice_backends/openai_realtime.py` for WebSocket/audio handling. OpenAI Realtime handles VAD, speech recognition, reasoning and speech generation. Coglet still owns local audio devices, robot animation, status LED, email delivery and graceful shutdown behavior.

## 6. Startup checks

Local Mode validates:

- `STT_URL` health endpoint
- configured `OLLAMA_MODEL` availability through `OLLAMA_URL`
- Piper/MQTT connectivity

Cloud Mode validates OpenAI Realtime settings and `websocket-client` availability without making paid Realtime API calls.

## Cloud Mode multilingual Realtime prompts

Cloud Mode loads OpenAI Realtime instructions in this order: `OPENAI_REALTIME_INSTRUCTIONS`, then `OPENAI_REALTIME_INSTRUCTIONS_FILE`, then `pi-side/prompts/openai-realtime-coglet-${COGLET_LANG}.txt`. German is the default if `COGLET_LANG` is unset or unsupported.
