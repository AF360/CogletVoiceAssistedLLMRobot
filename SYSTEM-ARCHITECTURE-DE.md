# Coglet - Local/Cloud-Architektur und Ports

## Local Mode

```text
Raspberry Pi
  coglet-local.py / local_mode.py
    -> XVF3800-Hardware-VAD-Sprachtrigger oder optionale Wakeword-Erkennung
    -> lokale Audioaufnahme + WebRTC VAD
    -> HTTP POST audio an den GPU-Server :5005 /stt
    -> Faster-Whisper transskribiert
    -> direkter Ollama Aufruf
    -> Sprachausgabe per Piper/MQTT TTS auf dem Raspberry Pi
    -> Roboter Animationen, Gesichtsverfolgung, LED, Tiefschlaf
```

Local Mode ist vollständig lokal/offline und verwendet OpenAI nicht.

## Cloud Mode

```text
Raspberry Pi
  coglet-cloud.py
    -> kontinuierliche OpenAI Realtime WebSocket Sitzung
    -> OpenAI kümmert sich um VAD, Spracherkennung, Denken und Sprachausgabe
    -> Raspberry Pi kümmert sich um Audio-Wiedergabe und Steuerung der Roboter Hardware
```

Cloud Mode wird explizit über `pi-side/coglet-cloud.py` gestartet. `coglet-local.py` bleibt ausschließlich lokal.

## Ports und Protokolle

| Richtung | Quelle | Ziel | Port | Protokoll | Zweck | Konfigurationsvariable |
|---|---|---|---:|---|---|---|
| Pi → GPU-Server | Raspberry Pi | GPU-Server | 5005 | HTTP | Faster-Whisper STT `/stt`, Health `/healthz` | `STT_URL` |
| Pi → GPU-Server | Raspberry Pi | GPU-Server | 11434 | HTTP | Ollama `/api/chat`, `/api/generate` | `OLLAMA_URL` |
| Pi ↔ Pi | Raspberry Pi | lokaler Broker | 1883 | MQTT | Piper-TTS-Befehl/Status/Cancel | `MQTT_BASE`, `PIPER_MQTT_*` |
| Pi → OpenAI | Raspberry Pi | OpenAI Realtime | 443 | WSS | nur Cloud Mode | `OPENAI_REALTIME_*` |

## Wichtige Local-Mode-Umgebungsvariablen

```bash
STT_URL=http://<gpu-server>:5005
OLLAMA_URL=http://<gpu-server>:11434
OLLAMA_MODEL=coglet:latest
COGLET_LANG=de
TTS_MODE=mqtt
PIPER_MQTT_HOST=127.0.0.1
PIPER_VOICE=/opt/piper/voices/de_DE-thorsten-high.onnx
PIPER_VOICE_JSON=/opt/piper/voices/de_DE-thorsten-high.onnx.json
```

Für den englischen Local Mode verwendet `local_mode.py` standardmäßig `en_US-lessac-high`, sofern dies nicht in der Umgebung überschrieben wird.

## Sprachbehandlung

`COGLET_LANG=de|en` ist der gemeinsame Sprachschalter. Local Mode wendet ihn auf lokale Prompt-/STT-/TTS-Defaults an. Cloud Mode wendet ihn auf die OpenAI-Realtime-Prompt-Dateiauswahl und cloudseitige Hilfstexte an. Standard ist Deutsch. Explizite OpenAI-Realtime-Instruktionsüberschreibungen bleiben explizit und hängen nicht von `COGLET_LANG` ab.
