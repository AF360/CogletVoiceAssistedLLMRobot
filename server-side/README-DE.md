# Coglet-Serverseite - Lokaler STT-Service

Das Verzeichnis `server-side` enthält die GPU-Server-Komponenten, die von **Coglet Local Mode** für Speech-to-Text und lokale Modell-Assets verwendet werden.

```text
Raspberry Pi: coglet-local.py
        -> HTTP-Anfrage an den GPU-Server
        -> Faster-Whisper STT
        -> JSON-Transkript-Antwort
        -> Ollama-Anfrage aus Local Mode
        -> Piper/MQTT TTS auf dem Raspberry Pi
```

Der dedizierte Cloud-Launcher `pi-side/coglet-cloud.py` verbindet sich direkt mit OpenAI Realtime und verwendet diesen serverseitigen STT-Service nicht.

Im serverseitigen Code gibt es kein OpenAI-Realtime-Backend und keinen serverseitigen TTS-Pfad.

## Hauptdateien

- `stt_http_server.py` — Flask-Service für Faster-Whisper STT
- `stt-http-server.service` — systemd-Unit für den STT-Service
- `INSTALLATION.md` — Debian/NVIDIA-Installationsanleitung
- `requirements.txt` — Python-Abhängigkeiten für die STT-Umgebung
- `Modelfile*` — Ollama-Coglet-Persona-/Modelldefinitionen

## Aktuelle API

Der STT-Server lauscht normalerweise auf TCP-Port `5005`.

### `POST /stt`

Primärer Endpunkt, der vom Raspberry Pi im Local Mode verwendet wird.

Akzeptierte Eingabe:

- Multipart-Feld `audio` mit WAV-/Audiodaten;
- optional `lang=de|en`.

Verarbeitung:

```text
Audio -> Faster-Whisper -> JSON-Transkript
```

Die Antwort liefert JSON mit `text`, `language` und `time_ms`.

### `GET /healthz`

Meldet Whisper-Modell, Gerät, Compute-Type, Sprachprompts und Tuning-Werte.

## Konfiguration

```bash
export STT_HTTP_PORT=5005
export LOG_LEVEL=INFO
export WHISPER_MODEL=large-v3-turbo
export WHISPER_DEVICE=cuda
export WHISPER_COMPUTE=float16
export STT_DEFAULT_LANG=de
export WHISPER_BEAM_SIZE=1
export WHISPER_VAD_MIN_SIL_MS=300
```

Piper wird auf der Raspberry-Pi-Seite konfiguriert. Für deutschen Local Mode verwendet `local_mode.py` standardmäßig:

```bash
/opt/piper/voices/de_DE-thorsten-high.onnx
/opt/piper/voices/de_DE-thorsten-high.onnx.json
```

Für englischen Local Mode verwendet `local_mode.py` standardmäßig:

```bash
/opt/piper/voices/en_US-lessac-high.onnx
/opt/piper/voices/en_US-lessac-high.onnx.json
```
