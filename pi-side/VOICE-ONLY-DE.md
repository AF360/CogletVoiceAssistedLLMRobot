# Lokale Sprachpipeline ohne Coglet-Hardware

`coglet-voice.py` startet dieselbe lokale Sprachpipeline wie
`coglet-local.py`, benötigt aber weder Servos noch PCA9685, Kamera,
Face-Tracking oder die XVF3800-Steuerschnittstelle. Damit lässt sich der im
Artikel beschriebene Aufbau mit einem Mikrofon und einem Lautsprecher auf dem
Tisch nachbauen.

Enthalten bleiben:

- OpenWakeWord und WebRTC-Endpunkterkennung,
- Faster-Whisper-STT über `STT_URL`,
- Ollama über `OLLAMA_URL`,
- Piper-TTS über MQTT beziehungsweise den vorhandenen Fallback,
- Gesprächskontext und Follow-up-Fenster,
- der lokale E-Mail-Pfad,
- die optionale Status-LED.

Nicht geladen werden Servo-, DOA-, Kamera-, Face-Tracking- und
Animationsmodule. Ein angeschlossenes XVF3800 kann weiterhin als normales
USB-Audiogerät dienen; seine Hardware-VAD- und DOA-Steuerschnittstellen werden
in diesem Launcher bewusst nicht verwendet.

## Start

Die vorhandene private Konfiguration kann weiterverwendet werden. Für einen
generischen Aufbau sollten mindestens Mikrofon, Lautsprecher und Wakeword
passend gesetzt werden:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh

export WAKEWORD_BACKEND="oww"
export MIC_DEVICE="default"
export SPEAKER_DEVICE="default"
export ENABLE_LED="0"

python3 coglet-voice.py
```

Für eine neue, reine Voice-Installation genügt statt der vollständigen
Pi-Abhängigkeitsliste:

```bash
python3 -m pip install -r requirements-voice.txt
```

Die darin auskommentierten Blinka-/NeoPixel-Pakete werden nur für die optionale
Status-LED benötigt.

Die Werte `STT_URL`, `OLLAMA_URL`, `OLLAMA_MODEL`, Piper/MQTT und das
OpenWakeWord-Modell entsprechen dem normalen Local Mode.

## Optionale Status-LED

Die LED ist im Voice-only-Modus standardmäßig ausgeschaltet. Zum Aktivieren:

```bash
export ENABLE_LED="1"
python3 coglet-voice.py
```

Ist `ENABLE_LED=1`, verwendet der Launcher die vorhandene
`hardware/status_led.py` und zeigt dieselben Zustände wie Coglet an:
Warten, Zuhören, Denken und Sprechen. Fehlen NeoPixel/Blinka oder die Hardware,
läuft die Sprachpipeline mit einer Warnung ohne LED weiter.

## Hinweis zum VAD

`WAKEWORD_BACKEND=xvf_vad` ist für diesen Launcher nicht sinnvoll, weil die
XVF3800-Steuerschnittstelle absichtlich nicht geladen wird. `local_mode.py`
fällt in diesem Fall zwar automatisch auf OpenWakeWord zurück; die explizite
Einstellung `WAKEWORD_BACKEND=oww` macht die Konfiguration jedoch eindeutig.
