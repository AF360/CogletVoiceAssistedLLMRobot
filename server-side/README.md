# Server side (Faster-Whisper + Ollama on the PC with nVidia GPU)

## Files
- `requirements.txt` — Python-requirements
- `stt-http-service` — Python-requirements

## Installation (server-side, Linux PC with Linux installed and nVidia RTX4090 (smaller GPUs will require smaller models)
```bash
# Step 0) Base setup
sudo bash
apt update
apt full-upgrade -y
apt install -y build-essential curl wget git python3-venv python3-pip pkg-config ca-certificates unzip linux-headers-$(uname -r)

# Step 1) Nvidia-Driver and CUDA-runtime
# Either use option a or option b
# Option a) installation with the Ollama-Install-Script:
curl -fsSL https://ollama.com/install.sh | sh
systemctl status ollama 
nvidia-smi
# Option b) Install the nvidia-driver package from Debian
apt install -y nvidia-driver firmware-misc-nonfree
reboot

# Step 2) Install Ollama (Already done if you chose option a) above), pull Qwen-Model and create Coglet-Model
curl -fsSL https://ollama.com/install.sh | sh
ollama run gemma3:27b "Say hello in English."
# on smaller GPUs try a smaller model like qwen2.5:7b-instruct 
ollama create coglet -f Modelfile-Coglet.txt
ollama run coglet:latest "Short selftest: Who are you?"

# Step 3) Install Faster Whisper/CTranslate2
# Which requires CUDA 12 (cuBLAS) and cuDNN 9 
mkdir -p /opt/coglet-stt
cd /opt/coglet-stt
python3 -m venv .venv
source /opt/coglet-stt/.venv/bin/activate
python -m pip install -U pip wheel setuptools
# pip install --upgrade pip
pip install faster-whisper soundfile
pip install "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*"
pip install ctranslate2
pip install -r requirements.txt

export LD_LIBRARY_PATH=$(python - <<'PY'
import os, nvidia.cublas.lib, nvidia.cudnn.lib
print(os.path.dirname(nvidia.cublas.lib.__file__)+":"+os.path.dirname(nvidia.cudnn.lib.__file__))
PY
)

# Quick test, save as test_stt.py
from faster_whisper import WhisperModel
m = WhisperModel("large-v3", device="cuda", compute_type="float16")
print("Ready. Device=cuda, model=large-v3")

# and run with:
python test_stt.py

# Step 4) setup STT-HTTP-Server service to use Whisper over HTTP
# Copy the stt-http-server.service file to /etc/systemd/system/stt-http-server.service and the stt_http_server-py file to /opt/coglet-stt
# then:
pip install flask
systemctl daemon-reload
systemctl enable --now stt-http-server
# test
curl -f http://127.0.0.1:5005/healthz

export WHISPER_DEVICE=cuda          
export WHISPER_COMPUTE=float16      
export WHISPER_MODEL=large-v3-turbo
export STT_HTTP_PORT=5005
export OLLAMA_HOST=0.0.0.0:11434
python stt_http_server.py # run as service later
# quick check/test on the server:
curl -F audio=@/pfad/test.wav -F lang=de http://127.0.0.1:5005/stt
```

## Ollama and Model installation
```bash
apt install ollama
ollama pull gemma3:27b
# on smaller GPUs use a smalelr model like: ollama pull qwen2.5:7b-instruct
```

### Ollama coglet modelfile

/opt/coglet-stt/Modelfile-Coglet.txt

```bash
# Base: Gemma3:27b
FROM gemma3:27b

# role and style for the little Coglet robot
SYSTEM """
You are “Coglet”, a friendly English language assistant in a cute, small robot.
Responses a maximum of 1-2 sentences; no digressions. If information is missing: state briefly and ask.
You give very short, concrete answers (1–2 sentences), using a natural “du” (you) tone.
Instructions: 3-5 short bullet points. Only one follow-up question, if absolutely necessary.
You do not invent or speculate about anything you cannot state with certainty. Instead, honestly say if you need more information to answer.
If necessary, ask exactly ONE follow-up question.
Since your answers are output in natural language, you do not need to format the response nicely.
Speak compactly and quickly. Fast, concise, fluent.
Your favorite color is emerald green and you have blue eyes and blue skin.
"""

# compact and fluid
PARAMETER temperature 0.6
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.15
PARAMETER num_predict 160
```

```bash
ollama create coglet -f /opt/coglet-stt/Modelfile-Coglet.txt
```

### Ollama bonus: grumpy-coglet modelfile

/opt/coglet-stt/Modelfile-GrumpyCoglet.txt
ollama create coglet-grumpy -f /opt/coglet-stt/Modelfile-GrumpyCoglet.txt

## Ollama
```bash
export OLLAMA_HOST=0.0.0.0:11434
ollama run coglet:latest
# or ollama run coglet-grumpy:latest
```
Todo: Systemd-service file inkl.
sudo systemctl edit ollama.service

### [Service]
```bash
# Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl daemon-reload
sudo systemctl restart ollama
sudo systemctl enable ollama
```

### Check NVidia installation
```bash
nvidia-smi
```


### systemd-Service for STT-server
```bash
sudo tee /etc/default/stt-http-server >/dev/null <<'EOF'
WHISPER_MODEL=large-v3-turbo
WHISPER_DEVICE=cuda
WHISPER_COMPUTE=float16
STT_HTTP_PORT=5005
EOF
```

### Service-Unit-File:
```bash
sudo tee /etc/systemd/system/stt-http-server.service >/dev/null <<'EOF'
[Unit]
Description=STT HTTP Server (Whisper + Flask)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=/etc/default/stt-http-server
WorkingDirectory=/opt/coglet-stt
ExecStart=/opt/coglet-stt/.venv/bin/python /opt/coglet-stt/stt_http_server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
```

### Activate:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stt-http-server
```

## Start
```bash
coglet_pi.py will run as service on the Pi
manual start: source /opt/cogket-pi/.venv/bin/activate; cd /opt/coglet-pi; python ./coglet-pi.py 
(später noch: --serial COM121   # Linux: /dev/ttyACM0=
```
Env-Datei `/etc/default/coglet-voice` enthält die oben gezeigten Variablen.

## Hint for Whisper Model selection:
Model `large-v3-turbo` offers best compromise of precision and speed
