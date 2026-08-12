# Coglet Local Mode server installation

This guide installs the GPU-server STT (Faster-Whisper) and Ollama with LLM components used by `pi-side/coglet-local.py`:

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

## Installing Ollama and the Coglet language model

Coglet uses [Ollama](https://ollama.com/) to run the language model locally. The following steps assume a Linux server using `systemd`. Start by running the commands from the repository's `server-side` directory.

### 1. Install Ollama

Install the current Ollama version:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Verify the installation:

```bash
ollama --version
```

The installer normally creates a dedicated user and the `ollama.service` systemd service. To allow the Raspberry Pi to access the Ollama API over the local network, Coglet includes the `ollama.service.d/override.conf` systemd drop-in. It extends the service file installed by Ollama without replacing it.

Install the supplied drop-in and restart Ollama:

```bash
sudo install -d -m 0755 /etc/systemd/system/ollama.service.d
sudo install -m 0644 ollama.service.d/override.conf \
  /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl enable ollama.service
sudo systemctl restart ollama.service
sudo systemctl status ollama.service
```

The line

```text
Environment=OLLAMA_HOST=0.0.0.0:11434
```

makes the Ollama API available on every network interface so that Coglet can access it from another computer, such as the Raspberry Pi. Use a firewall to limit access to the trusted local network, and do not expose port `11434` to the internet.

Use the following command to verify that systemd loaded the drop-in:

```bash
systemctl cat ollama.service
```

The end of the output must show the contents of `override.conf`, including `OLLAMA_HOST=0.0.0.0:11434`. The complete `ollama.service` file that is also present in the directory is not required for this installation method; the drop-in leaves Ollama's installed unit unchanged.

### 2. Download the base model

Download the base model used by Coglet:

```bash
ollama pull gemma4:12b-it-qat
```

The model is several gigabytes in size. Verify that it was downloaded successfully:

```bash
ollama list
```

The output must include `gemma4:12b-it-qat`.

### 3. Create the Coglet model from the Modelfile

By default, the Coglet server expects an Ollama model named `coglet:latest`. Create it from the English Modelfile:

```bash
ollama create coglet:latest -f Modelfile-Coglet-EN.txt
```

The Modelfile adds Coglet's English system prompt and the intended runtime parameters to the base model. Verify the result:

```bash
ollama list
```

The output must now include both `gemma4:12b-it-qat` and `coglet:latest`.

After changing the Modelfile, run the same `ollama create` command again. This updates the existing `coglet:latest` model tag.

### 4. Test the Coglet model

Before starting the Coglet server, perform a short interactive test:

```bash
ollama run coglet:latest
```

For example, enter `Hello Coglet!`. Then leave the interactive session with:

```text
/bye
```

### 5. Optional: preload the model at server startup

Without preloading, Ollama has to load the model into memory when it receives the first request. The supplied `ollama-warmup.sh` and `ollama-warmup.service` files can avoid this delay after a server restart. The script sends a test request to `coglet:latest` and uses `keep_alive` to retain the model in memory for 45 minutes.

Before installing the files, open `ollama-warmup.sh` and check the `URL` variable. The repository version contains an installation-specific IP address. Because the warm-up runs on the same server as Ollama, you will normally use the loopback address:

```bash
URL="http://127.0.0.1:11434/api/chat"
```

Then install and enable the script and service:

```bash
sudo install -m 0755 ollama-warmup.sh /usr/local/bin/ollama-warmup.sh
sudo install -m 0644 ollama-warmup.service /etc/systemd/system/ollama-warmup.service
sudo systemctl daemon-reload
sudo systemctl enable ollama-warmup.service
sudo systemctl start ollama-warmup.service
sudo systemctl status ollama-warmup.service
```

The warm-up service waits for `ollama.service` and retries the request up to ten times if Ollama starts slowly. View its log with:

```bash
journalctl -u ollama-warmup.service
```

Preloading is optional. For basic testing or on a system with limited GPU memory, disable `ollama-warmup.service` with:

```bash
sudo systemctl disable --now ollama-warmup.service
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
