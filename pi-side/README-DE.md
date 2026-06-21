# Raspberry-Pi-Seite: Setup & Code

## Betriebsmodi

Coglet hat zwei bewusst getrennte Launcher:

| Modus | Launcher | Verhalten |
|---|---|---|
| **Local Mode** | `coglet-local.py` | Hardware-VAD- oder optional Wakeword-getriggerte lokale STT/LLM/TTS-Pipeline über den GPU-Server |
| **Cloud Mode** | `coglet-cloud.py` | Kontinuierliche OpenAI-Realtime-Speech-to-Speech-Sitzung ohne Wakeword |

`coglet-local.py` ist ausschließlich lokal. Es enthält keinen OpenAI-Realtime-Ausführungspfad mehr. Um Cloud Mode zu verwenden, starte `coglet-cloud.py` explizit.

Der Launcher wählt den Modus.

## Dateien

- `coglet-local.py` — dedizierter Local-Mode-Launcher
- `local_mode.py` — lokale Hardware-VAD-/Wakeword-STT/LLM/TTS-Gesprächsimplementierung
- `coglet-cloud.py` — dedizierter kontinuierlicher OpenAI-Realtime-Launcher
- `robot_runtime.py` — öffentliche gemeinsame Robot-Hardware-Fassade
- `hardware/robot_runtime.py` — Servo-, LED-, Animations- und Face-Tracking-Runtime
- `voice_backends/openai_realtime.py` — Realtime-WebSocket-, Audio-Streaming-, VAD-Event- und Playback-Implementierung
- `prompts/openai-realtime-coglet-de.txt` — deutscher Realtime-Persona-/Systemprompt
- `env-exports.sh.example` — getrackte Vorlage mit gemeinsamer Local- und Cloud-Mode-Konfiguration
- `env-exports.sh` — private lokale Konfiguration, die von beiden Launchern gesourct wird; von Git ausgeschlossen
- `email_sender.py` — lokale SMTP-Zustellung mit HTML- und Plain-Text-MIME-Alternativen
- `piper_mqtt_tts.py` — Piper-Text-to-Speech-Bridge für kurze lokale Bestätigungsphrasen
- `piper_mqtt_tts.service` — systemd-Service-Definition für die Piper-MQTT-Bridge
- `piper_mqtt_tts` — Beispiel-Environment-Datei `/etc/default/piper_mqtt_tts`
- `say` — sendet Text an die Piper-MQTT-Bridge; typischerweise installiert als `/usr/local/bin/say`
- `say-cancel` — bricht Piper-Sprache über MQTT ab
- `requirements.txt` — Python-Abhängigkeiten
- `hardware/` — Raspberry-Pi-Audio-, Servo-, Status-LED-, Face-Tracking- und Kalibrierungsmodule
- `README.md` — diese Datei

## Installationspfad

Die Beispiele unten gehen aus von:

```text
/opt/coglet-pi
```

Private gemeinsame Umgebungsdatei einmalig erstellen und virtuelle Umgebung aktivieren:

```bash
cd /opt/coglet-pi
cp env-exports.sh.example env-exports.sh
vi env-exports.sh
source .venv/bin/activate
```

Beide Launcher sourcen dieselbe `env-exports.sh`. Das ausgewählte Executable bestimmt den Betriebsmodus.

## Local Mode

Manuell starten mit:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-local.py
```

Die Produktionsinstallation kann `coglet-local.py` stattdessen über ihren systemd-Service starten.

### Lokaler Gesprächspfad

```text
-> XVF3800-Hardware-VAD-Sprachtrigger oder optionale OpenWakeWord-Erkennung
-> kurze lokale Piper-Bestätigung, wenn OpenWakeWord-Modus genutzt wird
-> Mikrofon-Aufnahme bis Stille (700ms)
-> Faster-Whisper STT auf der GPU transkribiert
-> Ollama LLM auf der GPU erstellt Antwort
-> Piper/MQTT TTS auf dem Raspberry Pi gibt die Antwort als Sprache aus
-> Followup-Fenster beginnt
```

Local Mode:

- wartet standardmäßig auf den XVF3800-Hardware-VAD-Sprachtrigger oder, falls konfiguriert, auf OpenWakeWord;
- spielt die konfigurierte kurze Bestätigung, z. B. „Ja?“, nur wenn OpenWakeWord-Modus genutzt wird;
- nimmt Sprache auf, bis WebRTC VAD das Ende der Äußerung erkennt bzw. 700ms Stille;
- führt nach STT frühe lokale Befehls- und E-Mail-Anfrageerkennung aus;
- ruft das konfigurierte Ollama-Modell direkt für normale Gesprächsanfragen auf;
- spricht die generierte Antwort über die lokale Piper/MQTT-Bridge;
- hält während Follow-up-Turns lokale Gesprächshistorie;
- kehrt nach Follow-up-Stille in den Sprach-/Wake-Trigger-Wartezustand zurück;
- steuert Face Tracking, Servos, Animationen, Status-LED und Deep Sleep;
- kann angeforderte E-Mails über den lokalen Ollama- und SMTP-Pfad erzeugen und senden.

Local Mode benötigt keinen `OPENAI_API_KEY` und sendet kein Gesprächsaudio an OpenAI.

### Lokales E-Mail-Verhalten

Der Local-Mode-E-Mail-Pfad:

1. erkennt nach lokaler STT eine explizite E-Mail-Anfrage;
2. bittet das konfigurierte lokale Ollama-Modell, Betreff und HTML-Body zu erstellen;
3. sendet die E-Mail über `email_sender.py` und den lokal konfigurierten SMTP-Server;
4. spricht eine lokale Bestätigung und kehrt in den Sprach-/Wake-Trigger-Wartezustand zurück.

### Demo-Modus

Setzen:

```bash
export DEMOMODE=1
```

Demo Mode überspringt Sprach-/Wake-Trigger-Erkennung, Aufnahme, STT/LLM/TTS-Netzwerkverkehr, MQTT-Abhängigkeitschecks und normale Gespräche. Coglet führt nur Face Tracking, automatisches Blinzeln und periodische Animationen aus. Das Log enthält beim Start `Attention: Demomode active`.

## OpenAI Realtime Cloud Mode

Manuell starten mit:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-cloud.py
```

Speichere den echten API-Schlüssel, SMTP-Zugangsdaten und lokale Hardware-Overrides nur in der privaten `env-exports.sh`; diese Datei nicht committen.

`coglet-cloud.py` startet immer OpenAI Realtime. Es hat keinen Local-Mode-Pfad und fällt nicht auf `coglet-local.py` zurück.

### Cloud-Gesprächspfad

```text
Program start
    -> immediate continuous OpenAI Realtime session
    -> server-side VAD detects turns
    -> OpenAI speech recognition, reasoning and speech generation
    -> streamed PCM playback on the Raspberry Pi
    -> session remains open until shutdown
```

Cloud Mode hat kein Wakeword und verwendet nicht die lokale Faster-Whisper-, Ollama- oder Piper-Gesprächspipeline.

Der Raspberry Pi übernimmt weiterhin:

- ReSpeaker-Mikrofon- und Lautsprecher-I/O;
- Hardware-AEC/VAD/DoA-Integration, soweit verfügbar;
- Face Tracking, Status-LED und Robot-Animationen;
- Servo-Initialisierung und Parken;
- lokale SMTP-Zustellung für E-Mail-Tool-Calls;
- geordnetes Programmende.

### Realtime-Konfiguration

Typische Variablen in der gemeinsamen `env-exports.sh`:

```bash
export OPENAI_API_KEY=""
export OPENAI_REALTIME_MODEL="gpt-realtime-2"
export OPENAI_REALTIME_VOICE="marin"
export OPENAI_REALTIME_REASONING_EFFORT="low"
export OPENAI_REALTIME_VAD_MODE="server_vad"
export OPENAI_REALTIME_TRANSCRIPTION="true"
export OPENAI_REALTIME_TRANSCRIPTION_MODEL="gpt-4o-mini-transcribe"
export OPENAI_REALTIME_STARTUP_MESSAGE="Ich bin online und bereit zu helfen."
export OPENAI_REALTIME_EXIT_PHRASES="programm ende,programmende,programm-ende,coglet shutdown,coglet ausschalten,coglet beenden"
export OPENAI_REALTIME_SHUTDOWN_MESSAGE="Tschüss!"
export OPENAI_REALTIME_LOCAL_BARGE_IN="true"
export OPENAI_REALTIME_BARGE_IN_MIN_DBFS="-35"
export OPENAI_REALTIME_INSTRUCTIONS_FILE="/opt/coglet-pi/prompts/openai-realtime-coglet-de.txt"
```

Der Cloud-Launcher validiert `OPENAI_API_KEY`, die Abhängigkeit `websocket-client` und den konfigurierten Realtime-VAD-Modus, bevor er die Sitzung öffnet. Eine fehlende oder ungültige Anforderung stoppt Cloud Mode mit einem Fehler; er wechselt nie zu Local Mode.

Cloud Mode nutzt zusätzlich einen lokalen Barge-in-Detector, solange Assistenten-Audio abgespielt wird. Wenn lokal Sprache erkannt wird, stoppt Coglet die lokale `aplay`-Ausgabe sofort und bricht die aktive Realtime-Antwort ab. `OPENAI_REALTIME_BARGE_IN_MIN_DBFS`, `OPENAI_REALTIME_BARGE_IN_FRAMES` und `OPENAI_REALTIME_BARGE_IN_VAD_AGGRESSIVENESS` können angepasst werden, falls das ReSpeaker-/AEC-Setup zu empfindlich oder nicht empfindlich genug reagiert.

### Realtime-E-Mail-Tool

Cloud Mode registriert das Function Tool `send_email` bei OpenAI Realtime.

Wenn der Nutzer ausdrücklich darum bittet, Informationen per E-Mail zu senden:

1. erstellt das Realtime-Modell Betreff und strukturierten HTML-Body;
2. ruft das Modell `send_email(subject, body)` auf;
3. liest Coglet den festen Empfänger aus der lokalen `EMAIL_TO`-Konfiguration;
4. sendet der Pi die Nachricht über die vorhandene lokale SMTP-Implementierung;
5. wird das Tool-Ergebnis an Realtime zurückgegeben;
6. bestätigt die Realtime-Stimme den Erfolg oder meldet den Fehler.

Empfänger und SMTP-Zugangsdaten bleiben lokal und sind nicht modellgesteuert.

### Shutdown-Verhalten

`Ctrl+C`, `SIGTERM` oder ein konfigurierter gesprochener Shutdown-Befehl löst geordnetes Herunterfahren aus:

1. Face Tracking stoppt;
2. die aktive Realtime-Antwort wird abgebrochen oder darf sicher fertig werden;
3. Coglet spricht die konfigurierte Shutdown-Nachricht über die Realtime-Stimme;
4. die WebSocket-Sitzung schließt;
5. Sitzungsnutzung wird geloggt;
6. die Servos fahren in ihre kalibrierten Parkpositionen.

Ein zweites Shutdown-Signal erzwingt sofortige Beendigung.

### Sitzungsnutzungsstatistik

Beim Schließen der Sitzung loggt Cloud Mode aggregierte Nutzung aus allen `response.done`-Events:

```text
[openai-realtime] Session usage:
  Responses:                  21
  Input tokens:            32450
    Text:                  25434
    Audio:                  7016
    Cached:                25792
  Output tokens:            3010
    Text:                   1666
    Audio:                  1344
  Total tokens:            35460
  Session duration:     00:02:10
```

Input-Token-Summen stellen die API-Nutzung über Responses hinweg dar und können Gesprächskontext bei späteren Turns erneut enthalten. Gecachte Tokens werden separat angezeigt.

Cloud Mode sendet Gesprächsaudio an OpenAI und verursacht API-Kosten.

## Piper TTS auf dem Pi

Piper wird nicht direkt von `coglet-local.py` gestartet. Die separate MQTT-Bridge:

- abonniert `coglet/tts/say` und `coglet/tts/cancel`;
- ruft die Piper-CLI auf;
- schreibt temporäre WAV-Dateien unter `/run/piper/out`;
- veröffentlicht die Zustände `READY`, `START`, `SPEAKING`, `DONE`, `CANCELLED` und `ERROR`;
- unterstützt Abbruch über den Helfer `say-cancel`.

Piper wird für Local-Mode-Prompts, Bestätigungen und Hauptgesprächsantworten verwendet. Cloud-Mode-Antworten verwenden die ausgewählte OpenAI-Realtime-Stimme.

## Servo-Layout (PCA9685)

| Name | Funktion | Kanal |
| --- | --- | --- |
| `EYL` | Linkes Auge | 0 |
| `EYR` | Rechtes Auge | 1 |
| `LID` | Augenlid/Blinzeln | 2 |
| `NPT` | Kopf vor/zurück (Pitch) | 3 |
| `NRL` | Kopf links/rechts (Roll) | 4 |
| `MOU` | Mund öffnen/schließen | 5 |
| `EAL` | Linkes Ohr | 6 |
| `EAR` | Rechtes Ohr | 7 |
| `LWH` | Linkes Rad (Drehen auf der Stelle) | 8 |
| `RWH` | Rechtes Rad (Drehen auf der Stelle) | 9 |

Die Räder (`LWH`/`RWH`) drehen den Roboter auf der Stelle, wenn Augenbewegung allein nicht ausreicht, um den Nutzer im Blick zu behalten.

### Face Tracking und Räder konfigurieren

Die Face-Tracking-Pipeline bindet Augen-, Kopf- und Radservos automatisch an die PCA9685-Kanäle aus `SERVO_LAYOUT_V1`.

| Variable | Beschreibung |
| --- | --- |
| `FACE_TRACKING_WHEEL_CHANNELS` | Kommaseparierte Standardkanäle für linkes/rechtes Rad (`8,9`). |
| `FACE_TRACKING_WHEEL_LEFT_CHANNEL` / `FACE_TRACKING_WHEEL_RIGHT_CHANNEL` | Erzwingt einen Kanal für das jeweilige Rad. |
| `FACE_TRACKING_WHEEL_DEADZONE_DEG` | Augenwinkel-Offset, ab dem Radbewegung startet. |
| `FACE_TRACKING_WHEEL_FOLLOW_DELAY_S` | Verzögerung, bevor Räder auf anhaltende Augenabweichung reagieren. |
| `FACE_TRACKING_WHEEL_INPUT_MIN_DEG` / `FACE_TRACKING_WHEEL_INPUT_MAX_DEG` | Erwarteter Eingabebereich des Augenwinkels. |
| `FACE_TRACKING_WHEEL_OUTPUT_MIN_DEG` / `FACE_TRACKING_WHEEL_OUTPUT_MAX_DEG` | Zielwinkelbereich der Räder. |
| `FACE_TRACKING_WHEEL_POWER` | Form der nichtlinearen Mapping-Kurve. |

Lasse die Radkanalvariablen leer, wenn keine Radservos verbunden sind. Um Räder vollständig zu deaktivieren, setze `FACE_TRACKING_WHEEL_CHANNELS` und optionale Links/Rechts-Overrides auf leere Strings.

## PCA9685-Servo-Kalibrierung standalone

Verwenden:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python3 -m hardware.pca9685_servo_calibration   --channels 0-9   --output /opt/coglet-pi/servo_calibration.json
```

| Taste | Aktion |
| --- | --- |
| `<` / `>` | Zum gespeicherten Minimal-/Maximalwinkel bewegen |
| `c` | Zum Hardware-Neutralwinkel bewegen |
| `+` / `-` | Schrittgröße ändern |
| `u` / `d` | Um den gewählten Schritt bewegen |
| `U` / `D` | Aktuellen Winkel als Maximum/Minimum speichern |
| `x` | Aktuellen Kanal auf Werkseinstellungen zurücksetzen |
| `n` / `p` | Nächster/vorheriger Servokanal |
| `Q` | Kalibrierung speichern und beenden |

Die JSON-Kalibrierung enthält `min_deg`, `max_deg`, `start_deg` und, wo konfiguriert, kalibrierte Stop-/Parkwerte.

## Grove Vision AI Standalone-Test

Voraussetzungen:

- Grove-Vision-AI-Board per USB verbunden, normalerweise `/dev/ttyACM0`;
- Berechtigung für Zugriff auf das serielle Gerät, normalerweise über die Gruppe `dialout`.

Ausführen:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
python3 -m hardware.grove_vision_ai_standalone   --serial-port /dev/ttyACM0   --hz 2.0
```

| Flag | Beschreibung |
| --- | --- |
| `--serial-port` | Serielles Gerät, Standard `/dev/ttyACM0` |
| `--baud-rate` | Baudrate, Standard `921600` |
| `--hz` | Inferenzzyklen pro Sekunde |
| `--timeout` | Timeout pro Inferenzlauf |
| `--json` | Rohes JSON ausgeben |
| `--max-iterations` | Nach n Iterationen stoppen; `0` bedeutet unbegrenzt |

Erfolgreiche Ausgabe enthält erkannte Gesichter mit Confidence und Bounding-Box-Koordinaten. Timeouts oder leere Ergebnisse werden für Hardwarediagnose explizit gemeldet.

## Gemeinsame Robot-Runtime

`coglet-local.py` und `coglet-cloud.py` teilen sich physische Roboterhardware über `robot_runtime.py`; Befehlsnormalisierung liegt in `command_utils.py`. Der Cloud-Launcher lädt oder führt `coglet-local.py` nicht mehr aus.

## Sprachauswahl

Setze `COGLET_LANG=de` oder `COGLET_LANG=en` in `env-exports.sh`. Local Mode verwendet dies für STT-/TTS-/Prompt-Defaults. Cloud Mode verwendet denselben Schalter für OpenAI-Realtime-Prompt-Dateiauswahl und lokalisierte Cloud-Hilfstexte. Um die Sprachdefaults für Cloud Mode zu umgehen, setze `OPENAI_REALTIME_INSTRUCTIONS` oder `OPENAI_REALTIME_INSTRUCTIONS_FILE`.
