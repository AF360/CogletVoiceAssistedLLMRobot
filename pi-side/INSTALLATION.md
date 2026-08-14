# Coglet Raspberry Pi Installation

This guide describes how to install the Raspberry Pi side of Coglet under
`/opt/coglet-pi`.

It complements `README.md`: the README describes operating modes, configuration and
usage, while this document covers the actual base installation, including the Python
virtual environment, hardware dependencies, audio, Piper and MQTT.

> **Note about the current architecture**
>
> This guide documents the Local Mode currently represented in the repository, with
> local Piper and MQTT TTS on the Raspberry Pi. An alternative architecture with
> Piper on the GPU server may be added in the future.

## 1. Requirements

Recommended:

- Raspberry Pi 5
- 64-bit Raspberry Pi OS
- Network connectivity to the STT/Ollama server
- ReSpeaker/XVF3800 or another suitable ALSA audio device
- PCA9685 and the remaining Coglet hardware only for the full robot build
- Python 3 with `venv`

The examples assume the following installation directory:

```text
/opt/coglet-pi
```

Piper is installed separately under:

```text
/opt/piper
```

## 2. Update the system

```bash
sudo apt update
sudo apt full-upgrade -y
```

On a Raspberry Pi it can also be useful to update the firmware:

```bash
sudo rpi-eeprom-update -a
```

Reboot after kernel, firmware or bootloader updates:

```bash
sudo reboot
```

## 3. Install required system packages

```bash
sudo apt install -y \
    build-essential \
    ca-certificates \
    git \
    curl \
    wget \
    jq \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    alsa-utils \
    libasound2-dev \
    libportaudio2 \
    portaudio19-dev \
    libsndfile1 \
    i2c-tools \
    libgpiod-dev \
    python3-libgpiod \
    mosquitto \
    mosquitto-clients
```

`jq` and `mosquitto-clients` are used by the TTS helper scripts, among other
components.

## 4. Enable Raspberry Pi interfaces

The full Coglet hardware requires I2C, SPI and the serial hardware:

```bash
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_serial_hw 0
```

Optional, if needed:

```bash
sudo raspi-config nonint do_ssh 0
sudo raspi-config nonint do_camera 0
```

Check:

```bash
ls /dev/i2c* /dev/spi*
```

## 5. Install the Coglet files

Clone the repository to a temporary location:

```bash
cd /tmp
git clone https://github.com/AF360/CogletVoiceAssistedLLMRobot.git
```

Copy the Pi side to `/opt/coglet-pi`:

```bash
sudo mkdir -p /opt/coglet-pi
sudo cp -a /tmp/CogletVoiceAssistedLLMRobot/pi-side/. /opt/coglet-pi/
sudo chown -R "$USER":"$USER" /opt/coglet-pi
```

Then:

```bash
cd /opt/coglet-pi
```

## 6. Create the Python environment

For the full hardware build, the following Raspberry Pi setup is recommended:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
```

Update the Python tooling:

```bash
python -m pip install --upgrade pip setuptools wheel
```

### Full Coglet hardware

```bash
pip install -r requirements.txt
```

The current Pi setup additionally requires:

```bash
pip install rpi_ws281x
```

On Raspberry Pi 5, remove a classic `RPi.GPIO` package if it was installed through
`pip`:

```bash
pip uninstall -y RPi.GPIO
```

### Voice-only build

For the reduced voice-only launcher without servos, camera, XVF3800 control and GPIO
packages:

```bash
pip install -r requirements-voice.txt
```

See also:

```text
VOICE-ONLY-DE.md
```

## 7. Check audio devices

ALSA playback devices:

```bash
aplay -l
aplay -L
```

ALSA recording devices:

```bash
arecord -l
arecord -L
```

Python/PortAudio view:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python - <<'PY'
import sounddevice as sd
print(sd.query_devices())
PY
```

The example configuration uses:

```bash
export MIC_DEVICE="mic"
export SPEAKER_DEVICE="spk"
```

These names require matching ALSA device names or aliases. If the local system uses
different names, adjust `env-exports.sh` accordingly.

A simple playback test with an existing WAV file is:

```bash
aplay -D spk test.wav
```

## 8. Install Piper

The Piper/MQTT path currently used by the repository keeps Piper resident in memory,
so the voice model does not need to be loaded again for every sentence.

This operating mode requires a classic Piper CLI that continuously accepts text lines
through `stdin` and returns the generated WAV path through `stdout`.

Create the directories:

```bash
sudo mkdir -p /opt/piper/voices
sudo chown -R "$USER":"$USER" /opt/piper
cd /opt/piper
```

### Install the classic ARM64 binary

Example using the archived ARM64 release `2023.11.14-2`:

```bash
wget \
  https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz \
  -O /tmp/piper_linux_aarch64.tar.gz

tar -xzf /tmp/piper_linux_aarch64.tar.gz -C /tmp
cp -a /tmp/piper/. /opt/piper/
chmod +x /opt/piper/piper
```

Check:

```bash
/opt/piper/piper --help
```

## 9. Check Piper CLI compatibility

Special care is required here.

The current repository version starts Piper in `piper_mqtt_tts.py` using:

```text
--output_wav <directory>
```

The official classic Piper C++ CLI from release `2023.11.14-2` documents:

```text
--output_dir <directory>
```

Before changing a working Coglet installation, first inspect the **actual binary
installed on that Raspberry Pi**:

```bash
/opt/piper/piper --help 2>&1 | grep -E 'output(_|-)(wav|dir|file)'
```

You can also inspect the arguments of the running Piper process:

```bash
ps -ef | grep '[p]iper'
```

### Safe functional test

```bash
mkdir -p /tmp/piper-test
printf 'This is a Piper test.\n' | \
  /opt/piper/piper \
    --model /opt/piper/voices/de_DE-thorsten-high.onnx \
    --config /opt/piper/voices/de_DE-thorsten-high.onnx.json \
    --sentence_silence 0.06 \
    --output_dir /tmp/piper-test
```

A compatible persistent Piper build creates a WAV file and prints its path.

**Important:** Do not change a working Pi from `--output_wav` to `--output_dir`
solely because of this document. Test the Piper binary installed on that machine
first. For a fresh installation using the official `2023.11.14-2` binary shown
above, `--output_dir` is the matching CLI option.

## 10. Install the German Piper voice

Coglet currently uses `de_DE-thorsten-high` as its German default:

```bash
cd /opt/piper/voices

wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx

wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx.json
```

Check:

```bash
ls -lh \
  /opt/piper/voices/de_DE-thorsten-high.onnx \
  /opt/piper/voices/de_DE-thorsten-high.onnx.json
```

Optionally install `en_US-lessac-high` for English output:

```bash
wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx

wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx.json
```

## 11. Test Piper directly

A standalone test independent of MQTT:

```bash
printf 'Hello, I am Coglet.\n' | \
  /opt/piper/piper \
    --model /opt/piper/voices/de_DE-thorsten-high.onnx \
    --config /opt/piper/voices/de_DE-thorsten-high.onnx.json \
    --output_file /tmp/coglet-piper-test.wav
```

Then:

```bash
aplay -D spk /tmp/coglet-piper-test.wav
```

If `spk` is not available as an ALSA alias on the system, use a suitable device from
`aplay -L`.

## 12. Enable Mosquitto

```bash
sudo systemctl enable --now mosquitto
```

Check:

```bash
systemctl status mosquitto --no-pager
```

Simple local MQTT test:

Terminal 1:

```bash
mosquitto_sub -h 127.0.0.1 -t coglet/test
```

Terminal 2:

```bash
mosquitto_pub -h 127.0.0.1 -t coglet/test -m hello
```

## 13. Install the Piper/MQTT environment

Copy the supplied template:

```text
/opt/coglet-pi/piper_mqtt_tts
```

to `/etc/default`:

```bash
sudo cp /opt/coglet-pi/piper_mqtt_tts /etc/default/piper_mqtt_tts
sudo chmod 600 /etc/default/piper_mqtt_tts
sudo vi /etc/default/piper_mqtt_tts
```

Typical values:

```bash
PIPER_BIN=/opt/piper/piper
PIPER_MODEL=/opt/piper/voices/de_DE-thorsten-high.onnx
PIPER_CFG=/opt/piper/voices/de_DE-thorsten-high.onnx.json
PIPER_SENTENCE_SILENCE=0.06

SPEAKER_DEVICE=spk

MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_BASE=coglet/tts
```

For normal operation, for example:

```bash
LOG_LEVEL=INFO
```

## 14. Piper/MQTT systemd service

For a fresh installation, the bridge should run with the same Python virtual
environment as Coglet. This keeps `paho-mqtt`, `logging_setup` and the remaining
Coglet modules in one consistent installation tree.

Recommended service unit:

```ini
[Unit]
Description=Piper TTS (persistent, MQTT control+status)
After=network-online.target sound.target mosquitto.service
Wants=network-online.target mosquitto.service

[Service]
Type=simple
WorkingDirectory=/opt/coglet-pi
EnvironmentFile=/etc/default/piper_mqtt_tts
ExecStart=/opt/coglet-pi/.venv/bin/python /opt/coglet-pi/piper_mqtt_tts.py
Restart=always
RestartSec=0.5
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
```

Install:

```bash
sudo cp /opt/coglet-pi/piper_mqtt_tts.service \
  /etc/systemd/system/piper_mqtt_tts.service
```

If the repository service file does not yet match the unit shown above, adjust it
before enabling the service.

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piper_mqtt_tts.service
```

Status:

```bash
systemctl status piper_mqtt_tts.service --no-pager
```

Log:

```bash
journalctl -u piper_mqtt_tts.service -f
```

## 15. Install `say` and `say-cancel`

```bash
sudo install -m 0755 /opt/coglet-pi/say /usr/local/bin/say
sudo install -m 0755 /opt/coglet-pi/say-cancel /usr/local/bin/say-cancel
```

Test:

```bash
say "Hello, I am Coglet."
```

Cancel:

```bash
say-cancel
```

This tests MQTT, Piper and local audio playback together.

## 16. Create the private Coglet configuration

```bash
cd /opt/coglet-pi
cp env-exports.sh.example env-exports.sh
chmod 600 env-exports.sh
vi env-exports.sh
```

At minimum, verify or adjust:

```bash
export STT_URL="http://<GPU-SERVER>:5005"
export OLLAMA_URL="http://<GPU-SERVER>:11434"
export OLLAMA_MODEL="coglet:latest"

export COGLET_LANG="de"

export MIC_DEVICE="mic"
export MIC_SR="16000"
export MIC_CHANNELS="2"
export MIC_CHANNEL_INDEX="1"
export SPEAKER_DEVICE="spk"

export TTS_MODE="mqtt"
export PIPER_MQTT_HOST="127.0.0.1"
export PIPER_MQTT_PORT="1883"
export MQTT_BASE="coglet/tts"
```

For OpenWakeWord, also check in particular:

```bash
export WAKEWORD_BACKEND="oww"
export OWW_MODEL="/opt/coglet-pi/.venv/lib/python3.13/site-packages/openwakeword/resources/models/coglet.onnx"
```

The exact Python-version path may differ depending on the Raspberry Pi OS version:

```bash
python - <<'PY'
import openwakeword
print(openwakeword.__file__)
PY
```

Real SMTP credentials and, if used, the OpenAI API key belong only in the private
`env-exports.sh` and must not be committed to the repository.

## 17. Check server endpoints

### STT

```bash
curl -f http://<GPU-SERVER>:5005/healthz
```

If the installed STT service uses a different health endpoint, use the endpoint from
that service's local documentation.

### Ollama

```bash
curl -s http://<GPU-SERVER>:11434/api/tags | jq .
```

The configured model must exist, for example:

```text
coglet:latest
```

## 18. Start Local Mode manually

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-local.py
```

Local Mode uses:

```text
Raspberry Pi
  -> wakeword / hardware VAD
  -> recording / WebRTC VAD
  -> Faster-Whisper on the GPU server
  -> Ollama on the GPU server
  -> local Piper/MQTT
  -> ALSA playback
  -> mouth/robot animation
```

## 19. Start Cloud Mode manually

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-cloud.py
```

Cloud Mode uses OpenAI Realtime directly and does not require the local
Faster-Whisper/Ollama or Piper/MQTT path for its actual conversation pipeline.

See `README.md` for further details.

## 20. Tests

Python tests:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
pytest
```

Piper service:

```bash
systemctl is-active piper_mqtt_tts.service
```

Mosquitto:

```bash
systemctl is-active mosquitto
```

Audio:

```bash
aplay -L
arecord -L
```

I2C:

```bash
i2cdetect -y 1
```

## 21. Troubleshooting

### `ModuleNotFoundError`

Make sure the Coglet environment is active:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
```

### Piper service does not start

```bash
journalctl -u piper_mqtt_tts.service -n 100 --no-pager
```

Check:

```bash
/opt/piper/piper --help
ls -l /opt/piper/piper
ls -l /opt/piper/voices/
```

Also check the `--output_wav` versus `--output_dir` difference described under
**Check Piper CLI compatibility**.

### `say` produces no speech

Check MQTT:

```bash
mosquitto_sub -v -h 127.0.0.1 -t 'coglet/tts/#'
```

In parallel:

```bash
say "Test"
```

Service log:

```bash
journalctl -u piper_mqtt_tts.service -f
```

### Audio device not found

```bash
aplay -l
aplay -L
arecord -l
arecord -L
```

Then correct `MIC_DEVICE` and `SPEAKER_DEVICE` in `env-exports.sh`.

---

After successful installation, continue with `README.md`.
