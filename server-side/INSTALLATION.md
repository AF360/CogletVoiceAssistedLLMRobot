# Coglet Local Mode server installation

This guide installs the GPU-server STT component used by `pi-side/coglet-local.py`:

```text
Faster-Whisper STT
```

Local Mode uses Ollama for LLM responses and Piper/MQTT for TTS. Piper runs on the Raspberry Pi side, with the German default voice set to Thorsten.

The dedicated Cloud launcher `pi-side/coglet-cloud.py` connects directly to OpenAI Realtime and does not use this server.

## Requirements

- Debian 12 or comparable Linux distribution
- NVIDIA GPU with a recent driver
- Python 3 virtual environments
- Internet access for packages and model downloads
- shell access with `sudo`

## System preparation

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y build-essential curl wget git python3-venv python3-pip pkg-config ca-certificates unzip
```

## STT environment

```bash
sudo mkdir -p /opt/coglet-stt
sudo chown -R $USER:$USER /opt/coglet-stt
cd /opt/coglet-stt
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
pip install flask faster-whisper
```

Copy at least these repository files into `/opt/coglet-stt`:

```text
stt_http_server.py
requirements.txt
```

## Service environment

Create `/etc/default/coglet-stt`:

```bash
sudo vi /etc/default/coglet-stt
```

Example:

```bash
LOG_LEVEL=INFO
STT_HTTP_PORT=5005
WHISPER_MODEL=large-v3-turbo
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16
STT_DEFAULT_LANG=de
WHISPER_BEAM_SIZE=1
WHISPER_VAD_MIN_SIL_MS=300
WHISPER_CONDITION_ON_PREV=false
```

## Manual test

```bash
cd /opt/coglet-stt
source .venv/bin/activate
source /etc/default/coglet-stt
python3 stt_http_server.py
```

## systemd service

Create `/etc/systemd/system/coglet-stt.service`:

```ini
[Unit]
Description=Coglet Local STT Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/coglet-stt
EnvironmentFile=/etc/default/coglet-stt
ExecStart=/opt/coglet-stt/.venv/bin/python /opt/coglet-stt/stt_http_server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coglet-stt
sudo systemctl status coglet-stt
```

## API tests

```bash
curl -s http://127.0.0.1:5005/healthz
curl -F audio=@sample.wav -F lang=de http://127.0.0.1:5005/stt
```

## Raspberry Pi Local Mode configuration

```bash
STT_URL=http://GPU-SERVER:5005
OLLAMA_URL=http://GPU-SERVER:11434
TTS_MODE=mqtt
PIPER_MQTT_HOST=127.0.0.1
```

German default voice:

```bash
PIPER_VOICE=/opt/piper/voices/de_DE-thorsten-high.onnx
PIPER_VOICE_JSON=/opt/piper/voices/de_DE-thorsten-high.onnx.json
```

English default voice:

```bash
PIPER_VOICE=/opt/piper/voices/en_US-lessac-high.onnx
PIPER_VOICE_JSON=/opt/piper/voices/en_US-lessac-high.onnx.json
```
