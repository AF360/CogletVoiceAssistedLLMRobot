# Coglet Raspberry-Pi-Installation

Diese Anleitung beschreibt die Installation der Raspberry-Pi-Seite von Coglet unter
`/opt/coglet-pi`.

Sie ergänzt `README-DE.md`: Die README beschreibt Betriebsmodi, Konfiguration und
Verwendung; diese Datei beschreibt die eigentliche Grundinstallation einschließlich
virtueller Python-Umgebung, Hardware-Abhängigkeiten, Audio, Piper und MQTT.

> **Hinweis zum aktuellen Architekturstand**
>
> Diese Anleitung beschreibt den im Repository dokumentierten Local-Mode-Stand mit
> lokalem Piper und MQTT-TTS auf dem Raspberry Pi. Eine alternative Architektur mit
> Piper auf dem GPU-Server kann zukünftig hinzukommen.

## 1. Voraussetzungen

Empfohlen:

- Raspberry Pi 5
- 64-Bit Raspberry Pi OS
- Netzwerkverbindung zum STT-/Ollama-Server
- ReSpeaker/XVF3800 oder ein anderes geeignetes ALSA-Audiogerät
- PCA9685 und weitere Coglet-Hardware nur bei Verwendung der vollständigen
  Roboter-Variante
- Python 3 mit `venv`

Die Beispiele gehen von folgendem Installationspfad aus:

```text
/opt/coglet-pi
```

Piper wird separat unter folgendem Pfad installiert:

```text
/opt/piper
```

## 2. System aktualisieren

```bash
sudo apt update
sudo apt full-upgrade -y
```

Auf einem Raspberry Pi kann zusätzlich ein Firmware-Update sinnvoll sein:

```bash
sudo rpi-eeprom-update -a
```

Nach Kernel-, Firmware- oder Bootloader-Updates den Pi neu starten:

```bash
sudo reboot
```

## 3. Benötigte Systempakete installieren

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

`jq` sowie `mosquitto-clients` werden unter anderem von den TTS-Hilfsskripten
benötigt.

## 4. Raspberry-Pi-Schnittstellen aktivieren

Für die vollständige Coglet-Hardware werden I2C, SPI und die serielle Hardware
benötigt:

```bash
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_serial_hw 0
```

Optional, falls benötigt:

```bash
sudo raspi-config nonint do_ssh 0
sudo raspi-config nonint do_camera 0
```

Prüfen:

```bash
ls /dev/i2c* /dev/spi*
```

## 5. Coglet-Dateien installieren

Repository zunächst temporär klonen:

```bash
cd /tmp
git clone https://github.com/AF360/CogletVoiceAssistedLLMRobot.git
```

Pi-Seite nach `/opt/coglet-pi` kopieren:

```bash
sudo mkdir -p /opt/coglet-pi
sudo cp -a /tmp/CogletVoiceAssistedLLMRobot/pi-side/. /opt/coglet-pi/
sudo chown -R "$USER":"$USER" /opt/coglet-pi
```

Danach:

```bash
cd /opt/coglet-pi
```

## 6. Python-Umgebung anlegen

Für die vollständige Hardware-Variante empfiehlt sich auf dem Raspberry Pi:

```bash
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
```

Python-Werkzeuge aktualisieren:

```bash
python -m pip install --upgrade pip setuptools wheel
```

### Vollständige Coglet-Hardware

```bash
pip install -r requirements.txt
```

Zusätzlich wird auf dem aktuellen Pi-Setup benötigt:

```bash
pip install rpi_ws281x
```

Auf dem Raspberry Pi 5 sollte ein eventuell über `pip` installiertes klassisches
`RPi.GPIO` entfernt werden:

```bash
pip uninstall -y RPi.GPIO
```

### Voice-only-Variante

Für den reduzierten Voice-only-Launcher ohne Servos, Kamera,
XVF3800-Steuerung und GPIO-Pakete:

```bash
pip install -r requirements-voice.txt
```

Weitere Hinweise dazu stehen in:

```text
VOICE-ONLY-DE.md
```

## 7. Audio-Geräte prüfen

ALSA-Wiedergabegeräte:

```bash
aplay -l
aplay -L
```

ALSA-Aufnahmegeräte:

```bash
arecord -l
arecord -L
```

Python-/PortAudio-Sicht:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python - <<'PY'
import sounddevice as sd
print(sd.query_devices())
PY
```

Die Beispielkonfiguration verwendet:

```bash
export MIC_DEVICE="mic"
export SPEAKER_DEVICE="spk"
```

Diese Namen setzen passende ALSA-Gerätenamen bzw. Aliase voraus. Falls das lokale
System andere Namen verwendet, `env-exports.sh` entsprechend anpassen.

Eine einfache Wiedergabe lässt sich beispielsweise mit einer vorhandenen WAV-Datei
prüfen:

```bash
aplay -D spk test.wav
```

## 8. Piper installieren

Der aktuell im Repository verwendete Piper-MQTT-Pfad hält Piper persistent im
Speicher. Dadurch muss das Sprachmodell nicht für jeden Satz erneut geladen werden.

Für diesen Betriebsmodus wird die klassische Piper-CLI benötigt, die fortlaufend
Textzeilen über `stdin` annimmt und für jede erzeugte WAV-Datei deren Pfad über
`stdout` zurückliefert.

Verzeichnisse anlegen:

```bash
sudo mkdir -p /opt/piper/voices
sudo chown -R "$USER":"$USER" /opt/piper
cd /opt/piper
```

### Klassische ARM64-Binary installieren

Beispiel mit dem archivierten ARM64-Release `2023.11.14-2`:

```bash
wget \
  https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_aarch64.tar.gz \
  -O /tmp/piper_linux_aarch64.tar.gz

tar -xzf /tmp/piper_linux_aarch64.tar.gz -C /tmp
cp -a /tmp/piper/. /opt/piper/
chmod +x /opt/piper/piper
```

Prüfen:

```bash
/opt/piper/piper --help
```

## 9. Piper-CLI-Kompatibilität prüfen

Hier ist besondere Vorsicht sinnvoll.

Der aktuelle Repository-Stand startet Piper in `piper_mqtt_tts.py` mit:

```text
--output_wav <Verzeichnis>
```

Die offizielle klassische Piper-C++-CLI des Releases `2023.11.14-2` dokumentiert
dagegen:

```text
--output_dir <Verzeichnis>
```

Vor einer Änderung des funktionierenden Coglet-Systems zuerst die **tatsächlich auf
dem Pi installierte Binary** prüfen:

```bash
/opt/piper/piper --help 2>&1 | grep -E 'output(_|-)(wav|dir|file)'
```

Zusätzlich kann kontrolliert werden, mit welchen Argumenten der laufende Piper
gestartet wurde:

```bash
ps -ef | grep '[p]iper'
```

### Sicherer Funktionstest

```bash
mkdir -p /tmp/piper-test
printf 'Dies ist ein Piper-Test.\n' | \
  /opt/piper/piper \
    --model /opt/piper/voices/de_DE-ramona-low.onnx \
    --config /opt/piper/voices/de_DE-ramona-low.onnx.json \
    --sentence_silence 0.06 \
    --output_dir /tmp/piper-test
```

Ein kompatibler persistenter Piper-Build erzeugt eine WAV-Datei und gibt ihren Pfad
aus.

**Wichtig:** Einen funktionierenden bestehenden Pi nicht allein aufgrund dieser
Dokumentation von `--output_wav` auf `--output_dir` umstellen. Zuerst die dort
installierte Piper-Binary testen. Für eine Neuinstallation mit der oben genannten
offiziellen `2023.11.14-2`-Binary ist `--output_dir` der passende CLI-Schalter.

## 10. Deutsche Piper-Stimme installieren

Coglets deutscher Standard ist derzeit `de_DE-ramona-low`:

```bash
cd /opt/piper/voices

wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/ramona/low/de_DE-ramona-low.onnx

wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/ramona/low/de_DE-ramona-low.onnx.json
```

Prüfen:

```bash
ls -lh \
  /opt/piper/voices/de_DE-ramona-low.onnx \
  /opt/piper/voices/de_DE-ramona-low.onnx.json
```

Optional kann für englische Ausgabe `en_US-lessac-high` installiert werden:

```bash
wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx

wget \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx.json
```

## 11. Piper direkt testen

Ein Einzeltest unabhängig von MQTT:

```bash
printf 'Hallo, ich bin Coglet.\n' | \
  /opt/piper/piper \
    --model /opt/piper/voices/de_DE-ramona-low.onnx \
    --config /opt/piper/voices/de_DE-ramona-low.onnx.json \
    --output_file /tmp/coglet-piper-test.wav
```

Danach:

```bash
aplay -D spk /tmp/coglet-piper-test.wav
```

Falls `spk` auf dem System nicht als ALSA-Alias existiert, ein passendes Gerät aus
`aplay -L` verwenden.

## 12. Mosquitto aktivieren

```bash
sudo systemctl enable --now mosquitto
```

Status prüfen:

```bash
systemctl status mosquitto --no-pager
```

Ein einfacher lokaler MQTT-Test:

Terminal 1:

```bash
mosquitto_sub -h 127.0.0.1 -t coglet/test
```

Terminal 2:

```bash
mosquitto_pub -h 127.0.0.1 -t coglet/test -m hello
```

## 13. Piper-MQTT-Umgebung installieren

Die mitgelieferte Vorlage:

```text
/opt/coglet-pi/piper_mqtt_tts
```

nach `/etc/default` kopieren:

```bash
sudo cp /opt/coglet-pi/piper_mqtt_tts /etc/default/piper_mqtt_tts
sudo chmod 600 /etc/default/piper_mqtt_tts
sudo vi /etc/default/piper_mqtt_tts
```

Typische Werte:

```bash
PIPER_BIN=/opt/piper/piper
PIPER_MODEL=/opt/piper/voices/de_DE-ramona-low.onnx
PIPER_CFG=/opt/piper/voices/de_DE-ramona-low.onnx.json
PIPER_SENTENCE_SILENCE=0.06

SPEAKER_DEVICE=spk

MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_BASE=coglet/tts
```

Für normalen Betrieb kann beispielsweise verwendet werden:

```bash
LOG_LEVEL=INFO
```

## 14. Piper-MQTT-systemd-Service

Für eine Neuinstallation sollte die Bridge mit derselben virtuellen Python-Umgebung
wie Coglet gestartet werden. Dadurch stehen `paho-mqtt`, `logging_setup` und die
übrigen Coglet-Module konsistent zur Verfügung.

Empfohlene Service-Datei:

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

Installieren:

```bash
sudo cp /opt/coglet-pi/piper_mqtt_tts.service \
  /etc/systemd/system/piper_mqtt_tts.service
```

Falls die Repository-Service-Datei noch nicht dem oben gezeigten Stand entspricht,
vor dem Aktivieren entsprechend anpassen.

Danach:

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

## 15. `say` und `say-cancel` installieren

```bash
sudo install -m 0755 /opt/coglet-pi/say /usr/local/bin/say
sudo install -m 0755 /opt/coglet-pi/say-cancel /usr/local/bin/say-cancel
```

Test:

```bash
say "Hallo, ich bin Coglet."
```

Abbruch:

```bash
say-cancel
```

Damit wird zugleich MQTT, Piper und die lokale Audioausgabe getestet.

## 16. Private Coglet-Konfiguration anlegen

```bash
cd /opt/coglet-pi
cp env-exports.sh.example env-exports.sh
chmod 600 env-exports.sh
vi env-exports.sh
```

Mindestens prüfen bzw. anpassen:

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

Für OpenWakeWord außerdem insbesondere prüfen:

```bash
export WAKEWORD_BACKEND="oww"
export OWW_MODEL="/opt/coglet-pi/.venv/lib/python3.13/site-packages/openwakeword/resources/models/coglet.onnx"
```

Der genaue Python-Versionspfad kann je nach Raspberry-Pi-OS-Version abweichen:

```bash
python - <<'PY'
import openwakeword
print(openwakeword.__file__)
PY
```

Echte SMTP-Zugangsdaten und gegebenenfalls der OpenAI-API-Key gehören ausschließlich
in die private `env-exports.sh` und nicht ins Repository.

## 17. Server-Endpunkte prüfen

### STT

```bash
curl -f http://<GPU-SERVER>:5005/healthz
```

Falls der installierte STT-Service einen anderen Health-Endpunkt verwendet, dessen
lokale Service-Dokumentation verwenden.

### Ollama

```bash
curl -s http://<GPU-SERVER>:11434/api/tags | jq .
```

Das konfigurierte Modell muss vorhanden sein, beispielsweise:

```text
coglet:latest
```

## 18. Local Mode manuell starten

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-local.py
```

Der Local Mode verwendet:

```text
Raspberry Pi
  -> Wakeword / Hardware-VAD
  -> Aufnahme / WebRTC-VAD
  -> Faster-Whisper auf dem GPU-Server
  -> Ollama auf dem GPU-Server
  -> lokales Piper/MQTT
  -> ALSA-Wiedergabe
  -> Mund-/Roboteranimation
```

## 19. Cloud Mode manuell starten

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-cloud.py
```

Cloud Mode verwendet OpenAI Realtime direkt und benötigt für die eigentliche
Gesprächspipeline weder den lokalen Faster-Whisper-/Ollama- noch den
Piper-MQTT-Pfad.

Weitere Details stehen in `README-DE.md`.

## 20. Tests

Python-Tests:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
pytest
```

Piper-Service:

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

## 21. Fehlersuche

### `ModuleNotFoundError`

Sicherstellen, dass die Coglet-Umgebung aktiv ist:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
```

Danach:

```bash
pip install -r requirements.txt
```

### Piper-Service startet nicht

```bash
journalctl -u piper_mqtt_tts.service -n 100 --no-pager
```

Prüfen:

```bash
/opt/piper/piper --help
ls -l /opt/piper/piper
ls -l /opt/piper/voices/
```

Außerdem den unter **Piper-CLI-Kompatibilität prüfen** beschriebenen Unterschied
zwischen `--output_wav` und `--output_dir` kontrollieren.

### `say` erzeugt keine Sprache

MQTT prüfen:

```bash
mosquitto_sub -v -h 127.0.0.1 -t 'coglet/tts/#'
```

Parallel:

```bash
say "Test"
```

Service-Log:

```bash
journalctl -u piper_mqtt_tts.service -f
```

### Audio-Gerät nicht gefunden

```bash
aplay -l
aplay -L
arecord -l
arecord -L
```

Danach `MIC_DEVICE` und `SPEAKER_DEVICE` in `env-exports.sh` korrigieren.

---

Nach erfolgreicher Installation mit `README-DE.md` fortfahren.
