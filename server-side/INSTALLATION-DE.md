# Installation des Coglet-Local-Mode-Servers (Faster-Whisper und Ollama)

Diese Anleitung installiert die GPU-Server-STT-Komponente (Faster-Whisper) und Ollama sowie LLM, welche von `pi-side/coglet-local.py` verwendet werden:

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

## Ollama und das Coglet-Sprachmodell installieren

Coglet verwendet [Ollama](https://ollama.com/) zur lokalen Ausführung des Sprachmodells. Die folgenden Schritte sind für einen Linux-Server mit `systemd` vorgesehen. Führen Sie die Befehle zunächst im Verzeichnis `server-side` des Repositorys aus.

### 1. Ollama installieren

Installieren Sie die aktuelle Ollama-Version:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Prüfen Sie anschließend die Installation:

```bash
ollama --version
```

Ollama richtet dabei normalerweise bereits einen eigenen Benutzer und den `systemd`-Dienst `ollama.service` ein. Damit der Raspberry Pi über das lokale Netzwerk auf die Ollama-API zugreifen kann, enthält Coglet den systemd-Drop-in `ollama.service.d/override.conf`. Er ergänzt die von Ollama installierte Service-Datei, ohne sie zu ersetzen.

Installieren Sie den mitgelieferten Drop-in und starten Sie Ollama neu:

```bash
sudo install -d -m 0755 /etc/systemd/system/ollama.service.d
sudo install -m 0644 ollama.service.d/override.conf \
  /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl enable ollama.service
sudo systemctl restart ollama.service
sudo systemctl status ollama.service
```

Die Zeile

```text
Environment=OLLAMA_HOST=0.0.0.0:11434
```

macht die Ollama-API auf allen Netzwerkschnittstellen erreichbar, damit Coglet von einem anderen Rechner – beispielsweise dem Raspberry Pi – darauf zugreifen kann. Begrenzen Sie den Zugriff deshalb per Firewall auf das vertrauenswürdige lokale Netz und geben Sie Port `11434` nicht ins Internet frei.

Mit folgendem Befehl können Sie kontrollieren, ob systemd den Drop-in geladen hat:

```bash
systemctl cat ollama.service
```

Am Ende der Ausgabe muss der Inhalt von `override.conf` mit `OLLAMA_HOST=0.0.0.0:11434` erscheinen. Die ebenfalls im Verzeichnis vorhandene vollständige Datei `ollama.service` ist für diesen Installationsweg nicht erforderlich; der Drop-in lässt die von Ollama installierte Unit unangetastet.

### 2. Basismodell herunterladen

Laden Sie das von Coglet verwendete Basismodell herunter:

```bash
ollama pull gemma4:12b-it-qat
```

Das Modell umfasst mehrere Gigabyte. Prüfen Sie nach dem Download, ob es vollständig vorhanden ist:

```bash
ollama list
```

In der Ausgabe muss `gemma4:12b-it-qat` erscheinen.

### 3. Coglet-Modell aus dem Modelfile erzeugen

Der Coglet-Server erwartet standardmäßig ein Ollama-Modell mit dem Namen `coglet:latest`. Erzeugen Sie es aus dem deutschen Modelfile:

```bash
ollama create coglet:latest -f Modelfile-Coglet-DE.txt
```

Das Modelfile ergänzt das Basismodell unter anderem um Coglets deutschen Systemprompt und die vorgesehenen Laufzeitparameter. Prüfen Sie anschließend das Ergebnis:

```bash
ollama list
```

In der Ausgabe müssen nun sowohl `gemma4:12b-it-qat` als auch `coglet:latest` aufgeführt sein.

Nach Änderungen am Modelfile führen Sie denselben `ollama create`-Befehl erneut aus. Der vorhandene Modell-Tag `coglet:latest` wird dabei aktualisiert.

### 4. Coglet-Modell testen

Starten Sie vor dem Coglet-Server einen kurzen interaktiven Funktionstest:

```bash
ollama run coglet:latest
```

Geben Sie beispielsweise `Hallo Coglet!` ein. Beenden Sie den Dialog anschließend mit:

```text
/bye
```

### 5. Optional: Modell beim Serverstart vorladen

Ohne Vorladen muss Ollama das Modell bei der ersten Anfrage in den Speicher laden. Die mitgelieferten Dateien `ollama-warmup.sh` und `ollama-warmup.service` können diese Verzögerung nach einem Serverneustart vermeiden. Das Skript sendet eine Testanfrage an `coglet:latest` und hält das Modell mit `keep_alive` für 45 Minuten im Speicher.

Öffnen Sie vor der Installation `ollama-warmup.sh` und prüfen Sie die Variable `URL`. Die Repository-Fassung enthält eine installationsspezifische IP-Adresse. Da das Warm-up auf demselben Server wie Ollama ausgeführt wird, verwenden Sie normalerweise die lokale Adresse:

```bash
URL="http://127.0.0.1:11434/api/chat"
```

Installieren und aktivieren Sie anschließend Skript und Dienst:

```bash
sudo install -m 0755 ollama-warmup.sh /usr/local/bin/ollama-warmup.sh
sudo install -m 0644 ollama-warmup.service /etc/systemd/system/ollama-warmup.service
sudo systemctl daemon-reload
sudo systemctl enable ollama-warmup.service
sudo systemctl start ollama-warmup.service
sudo systemctl status ollama-warmup.service
```

Der Warm-up-Dienst wartet auf `ollama.service` und versucht die Anfrage bei einem verzögerten Ollama-Start bis zu zehnmal. Sein Protokoll lässt sich wie folgt anzeigen:

```bash
journalctl -u ollama-warmup.service
```

Das Vorladen ist optional, aber empfohlen. Für einen Funktionstest oder bei knappem GPU-Speicher kann `ollama-warmup.service` deaktiviert werden:

```bash
sudo systemctl disable --now ollama-warmup.service
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
