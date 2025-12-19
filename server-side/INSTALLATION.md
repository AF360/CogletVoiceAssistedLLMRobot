# Coglet Server Setup (Debian 12 + NVIDIA)

From zero to a working stack with **Ollama** (Qwen2.5 → Coglet) and **Faster‑Whisper** (GPU).  
Tested on Debian 12 (“Bookworm”) with NVIDIA GPUs (≥ 8–12 GB VRAM).

---

## 1) Requirements

- Debian 12 (fresh or existing)
- NVIDIA GPU + recent driver
- Internet access for packages & model downloads
- Shell access with `sudo`

---

## 2) System prep

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y build-essential curl wget git python3-venv python3-pip pkg-config   ca-certificates unzip linux-headers-$(uname -r)
```

> If you use SecureBoot or special kernels, consult Debian’s NVIDIA notes before installing the driver.

---

## 3) Install Ollama (creates a systemd service)

Run the official installer:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

This installs the binaries and (when `systemd` is available/running) **creates and enables** `ollama.service` (user/group `ollama`) and starts it.

Quick checks:

```bash
systemctl status ollama
ollama -v
```

### (Optional) Customize the service

Use a systemd override to set environment variables like `OLLAMA_HOST`, `OLLAMA_NUM_PARALLEL`, etc.:

```bash
sudo systemctl edit ollama
# then add, for example:
# [Service]
# Environment="OLLAMA_HOST=127.0.0.1:11434"
# Environment="OLLAMA_NUM_PARALLEL=2"
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

> The API listens on `127.0.0.1:11434` by default. Only expose it deliberately and behind a firewall/reverse proxy.

---

## 4) NVIDIA driver (if not already present)

You can let the installer pull CUDA runtime pieces on Debian, or install Debian’s driver directly:

```bash
sudo apt install -y nvidia-driver firmware-misc-nonfree
sudo reboot
# after reboot:
nvidia-smi
```

You should see your GPU and a CUDA version listed.

---

## 5) Create the Coglet model from Gemma3

Test the base model first:

```bash
ollama run gemma3:27bt "Say hello in English."
```

Create a `Modelfile` (adjust prompt/params as you like):

```bash
FROM gemma3:27b

SYSTEM """
You are Coglet, a local LAN voice assistant. Answer precisely and concisely in German.
"""

PARAMETER num_ctx 8192
PARAMETER temperature 0.3
```

Build & test the derived model:

```bash
ollama create coglet -f Modelfile
ollama run coglet "Quick test: Who are you?"
```

---

## 6) Install Faster‑Whisper (GPU)

Faster‑Whisper uses **CTranslate2**. For GPU you need **CUDA 12 cuBLAS** and **cuDNN 9**. On Linux the simplest route is to use the PyPI wheels inside a virtualenv.

### Create a dedicated service user and project folder

```bash
sudo useradd -r -m -s /usr/sbin/nologin coglet
sudo -u coglet bash -lc 'mkdir -p ~/stt && python3 -m venv ~/stt/.venv'
sudo -u coglet bash -lc '~/stt/.venv/bin/pip install --upgrade pip'
```

### Install packages inside the venv

```bash
# as the coglet user
sudo -u coglet bash -lc '~/stt/.venv/bin/pip install faster-whisper flask "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*"'
```

> If you later hit missing NVRTC errors, also install: `nvidia-cuda-nvrtc-cu12` in the same venv.

### Optional quick GPU check

Create `~/stt/test_stt.py`:

```python
from faster_whisper import WhisperModel
m = WhisperModel("large-v3", device="cuda", compute_type="float16")
print("Ready on CUDA.")
```

Run it:

```bash
sudo -u coglet bash -lc '~/stt/.venv/bin/python ~/stt/test_stt.py'
```

---

## 7) Minimal STT HTTP server (Flask) + systemd

Copy stt_http_server.py from the server-side source-directory to `~/stt/stt_http_server.py` (run as `coglet` user).

Create a tiny launcher `~/stt/run_stt.sh` to set `LD_LIBRARY_PATH` dynamically:

```bash
#!/usr/bin/env bash
set -euo pipefail

VENV="$HOME/stt/.venv"
export PATH="$VENV/bin:$PATH"

# Discover cuBLAS/cuDNN inside the venv (PyPI wheels)
export LD_LIBRARY_PATH="$(
  python - <<'PY'
import os, nvidia.cublas.lib, nvidia.cudnn.lib
print(os.path.dirname(nvidia.cublas.lib.__file__)+":"+os.path.dirname(nvidia.cudnn.lib.__file__))
PY
):${LD_LIBRARY_PATH:-}"

exec python "$HOME/stt/stt_http_server.py"
```

Make it executable:

```bash
sudo -u coglet bash -lc 'chmod +x ~/stt/run_stt.sh'
```

Create the systemd unit `/etc/systemd/system/stt-http-server.service`:

```ini
[Unit]
Description=STT HTTP Server (Faster-Whisper + Flask)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=coglet
WorkingDirectory=/home/coglet/stt
ExecStart=/usr/bin/env bash /home/coglet/stt/run_stt.sh
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable & test:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stt-http-server
curl -f http://127.0.0.1:5005/healthz
```

---

## 8) Quick end‑to‑end tests

- **LLM**  
  ```bash
  ollama run coglet "List three advantages of Debian 12."
  ```

- **STT**  
  ```bash
  curl -F "audio=@sample.wav" http://127.0.0.1:5005/stt
  ```

> Note: Faster‑Whisper decodes audio with **PyAV** (bundled FFmpeg libs). You typically don’t need a system `ffmpeg` just to read common formats.

---

## 9) Troubleshooting

- **`libcublas.so.12` / cuDNN not found**  
  Ensure the wheels `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` are installed **in the same venv** as Faster‑Whisper and that `LD_LIBRARY_PATH` points to their `.../site-packages/nvidia/*/lib` dirs (the provided `run_stt.sh` handles this).

- **Ollama service tuning / logs**  
  Use `sudo systemctl edit ollama` for overrides. View logs via `journalctl -e -u ollama`.

- **Verify GPU**  
  `nvidia-smi` should list the card and CUDA version.

- **Port binding**  
  If another process is using `:11434` (Ollama) or `:5005` (STT), adjust the ports and restart the services.

---

## 10) Security notes

- Keep Ollama’s API (`:11434`) **local** unless you intentionally expose it behind a firewall/reverse proxy.
- Rate-limit and/or authenticate the STT endpoint before exposing it beyond localhost.
- Regularly update models and Python packages.

---

## 11) Summary of what gets installed

- **Ollama** service (`ollama.service`) providing a local LLM API at `127.0.0.1:11434`
- **Coglet** model derived from `qwen2.5:7b-instruct` via `Modelfile`
- **Faster‑Whisper** GPU stack in a Python venv under `/home/coglet/stt`
- **STT HTTP** service (`stt-http-server.service`) exposing `/stt` and `/healthz` on port `5005`


