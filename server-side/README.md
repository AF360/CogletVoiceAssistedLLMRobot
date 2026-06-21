# Coglet server side — Local STT service

The `server-side` directory contains the GPU-server components used by **Coglet Local Mode** for speech-to-text and local model assets.

```text
Raspberry Pi: coglet-local.py
        -> HTTP request to GPU server
        -> Faster-Whisper STT
        -> JSON transcript response
        -> Ollama request from Local Mode
        -> Piper/MQTT TTS on the Raspberry Pi
```

The dedicated Cloud launcher `pi-side/coglet-cloud.py` connects directly to OpenAI Realtime and does not use this server-side STT service.

There is no OpenAI Realtime backend or server-side TTS path in the server-side code.

## Main files

- `stt_http_server.py` — Flask service for Faster-Whisper STT
- `stt-http-server.service` — systemd unit for the STT service
- `INSTALLATION.md` — Debian/NVIDIA installation guide
- `requirements.txt` — Python dependencies for the STT environment
- `Modelfile*` — Ollama Coglet persona/model definitions

## Current API

The STT server normally listens on TCP port `5005`.

### `POST /stt`

Primary endpoint used by Raspberry Pi Local Mode.

Accepted input:

- multipart field `audio` containing WAV/audio data;
- optional `lang=de|en`.

Processing:

```text
audio -> Faster-Whisper -> JSON transcript
```

The response returns JSON with `text`, `language` and `time_ms`.

### `GET /healthz`

Reports Whisper model, device, compute type, language prompts and tuning values.

## Configuration

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

Piper is configured on the Raspberry Pi side. For German Local Mode, `local_mode.py` defaults to:

```bash
/opt/piper/voices/de_DE-thorsten-high.onnx
/opt/piper/voices/de_DE-thorsten-high.onnx.json
```

For English Local Mode, `local_mode.py` defaults to:

```bash
/opt/piper/voices/en_US-lessac-high.onnx
/opt/piper/voices/en_US-lessac-high.onnx.json
```
