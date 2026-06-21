# Installation des Coglet-Local-Mode-Servers

Diese Anleitung installiert die GPU-Server-STT-Komponente, die von `pi-side/coglet-local.py` verwendet wird:

```text
Faster-Whisper STT
```

Local Mode verwendet Ollama für LLM-Antworten und Piper/MQTT für TTS. Piper läuft auf der Raspberry-Pi-Seite, mit Thorsten als deutschem Standard-Voice.

Der dedizierte Cloud-Launcher `pi-side/coglet-cloud.py` verbindet sich direkt mit OpenAI Realtime und verwendet diesen Server nicht.

## Anforderungen

- Debian 12 oder vergleichbare Linux-Distribution
- NVIDIA-GPU mit aktuellem Treiber
- Python-3-virtuelle Umgebungen
- Internetzugang für Pakete und Modelldownloads
- Shell-Zugriff mit `sudo`

## Systemvorbereitung

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y build-essential curl wget git python3-venv python3-pip pkg-config ca-certificates unzip
```

## STT-Umgebung

```bash
sudo mkdir -p /opt/coglet-stt
sudo chown -R $USER:$USER /opt/coglet-stt
cd /opt/coglet-stt
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
pip install flask faster-whisper
```

Kopiere mindestens diese Repository-Dateien nach `/opt/coglet-stt`:

```text
stt_http_server.py
requirements.txt
```

## Service-Umgebung

Erstelle `/etc/default/coglet-stt`:

```bash
sudo vi /etc/default/coglet-stt
```

Beispiel:

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

## Manueller Test

```bash
cd /opt/coglet-stt
source .venv/bin/activate
source /etc/default/coglet-stt
python3 stt_http_server.py
```

## systemd-Service

Erstelle `/etc/systemd/system/coglet-stt.service`:

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

Aktivieren und starten:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coglet-stt
sudo systemctl status coglet-stt
```

## API-Tests

```bash
curl -s http://127.0.0.1:5005/healthz
curl -F audio=@sample.wav -F lang=de http://127.0.0.1:5005/stt
```

## Raspberry-Pi-Local-Mode-Konfiguration

```bash
STT_URL=http://GPU-SERVER:5005
OLLAMA_URL=http://GPU-SERVER:11434
TTS_MODE=mqtt
PIPER_MQTT_HOST=127.0.0.1
```

Deutsche Standardstimme:

```bash
PIPER_VOICE=/opt/piper/voices/de_DE-thorsten-high.onnx
PIPER_VOICE_JSON=/opt/piper/voices/de_DE-thorsten-high.onnx.json
```

Englische Standardstimme:

```bash
PIPER_VOICE=/opt/piper/voices/en_US-lessac-high.onnx
PIPER_VOICE_JSON=/opt/piper/voices/en_US-lessac-high.onnx.json
```
