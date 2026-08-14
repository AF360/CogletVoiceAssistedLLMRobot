# Coglet – Local/Cloud architecture and ports

## Local Mode

```text
Raspberry Pi
  coglet-local.py / local_mode.py
    -> XVF3800 hardware VAD speech trigger or optional wakeword detection
    -> local recording + WebRTC VAD
    -> HTTP POST audio to Aurora :5005 /stt
    -> Faster-Whisper transcript
    -> direct Ollama request
    -> Piper/MQTT TTS on the Raspberry Pi
    -> robot animation, face tracking, LED, deep sleep
```

Local Mode is fully local/offline and does not use OpenAI.

## Cloud Mode

```text
Raspberry Pi
  coglet-cloud.py
    -> continuous OpenAI Realtime WebSocket session
    -> OpenAI handles VAD, STT, reasoning and speech output
    -> Raspberry Pi handles audio playback and robot hardware
```

Cloud Mode is started explicitly through `pi-side/coglet-cloud.py`. `coglet-local.py` stays local-only.

## Ports and protocols

| Direction | Source | Destination | Port | Protocol | Purpose | Config variable |
|---|---|---|---:|---|---|---|
| Pi → Aurora | Raspberry Pi | GPU server | 5005 | HTTP | Faster-Whisper STT `/stt`, health `/healthz` | `STT_URL` |
| Pi → Aurora | Raspberry Pi | GPU server | 11434 | HTTP | Ollama `/api/chat`, `/api/generate` | `OLLAMA_URL` |
| Pi ↔ Pi | Raspberry Pi | local broker | 1883 | MQTT | Piper TTS command/status/cancel | `MQTT_BASE`, `PIPER_MQTT_*` |
| Pi → OpenAI | Raspberry Pi | OpenAI Realtime | 443 | WSS | Cloud Mode only | `OPENAI_REALTIME_*` |

## Key Local Mode environment variables

```bash
STT_URL=http://<aurora>:5005
OLLAMA_URL=http://<aurora>:11434
OLLAMA_MODEL=coglet:latest
COGLET_LANG=de
TTS_MODE=mqtt
PIPER_MQTT_HOST=127.0.0.1
PIPER_VOICE=/opt/piper/voices/de_DE-thorsten-high.onnx
PIPER_VOICE_JSON=/opt/piper/voices/de_DE-thorsten-high.onnx.json
```

For English Local Mode, `local_mode.py` defaults to `en_US-lessac-high` unless overridden in the environment.

## Language handling

`COGLET_LANG=de|en` is the shared language switch. Local Mode applies it to local prompt/STT/TTS defaults. Cloud Mode applies it to OpenAI Realtime prompt-file selection and cloud-side helper texts. The default is German. OpenAI Realtime instruction overrides remain explicit and do not depend on `COGLET_LANG`.
