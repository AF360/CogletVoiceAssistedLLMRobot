![Coglet1](images/Cogletblue2.jpg)  

# Coglet VoiceAssistedLLM Robot

Ein Roboter-/Animatronics-Prototyp mit Voice-I/O, animierten Augen- und Kopfbewegungen, Face Tracking sowie Online- und Offline-Gesprächsmodi:

| Modus | Launcher | Gesprächspipeline |
|---|---|---|
| **Local Mode** | `pi-side/coglet-local.py` | Hardware-VAD oder optionales Wakeword -> lokale Aufnahme -> Faster-Whisper -> Ollama -> Piper/MQTT TTS |
| **Cloud Mode** | `pi-side/coglet-cloud.py` | Kontinuierliche OpenAI-Realtime-Speech-to-Speech-Sitzung ohne Wakeword |

Die Launcher sind absichtlich getrennt. `coglet-local.py` ist ausschließlich lokal; es enthält keinen OpenAI-Realtime-Ausführungspfad. Um OpenAI Realtime zu verwenden, starte stattdessen `coglet-cloud.py`.

Beide Launcher verwenden dieselbe private Datei `pi-side/env-exports.sh`. Das ausgewählte Executable bestimmt den Modus; es gibt keinen Runtime-Backend-Selector.

## Local Mode

Local Mode hält das Gespräch lokal. Standardmäßig kann Coglet über das Hardware-VAD des XVF3800 geweckt werden; OpenWakeWord bleibt als optionaler Wakeword-Trigger verfügbar.

```text
-> Hardware-VAD-Sprachtrigger oder optionale OpenWakeWord-Erkennung
-> gesprochene Eingabe
-> Faster-Whisper STT auf dem GPU-Server transkribiert
-> Text geht an das Ollama LLM
-> Text-Antwort geht per MQTT an den Piper-TTS auf dem Raspberry Pi in Coglet
-> gesprochene Ausgabe
-> Follow-up-Fenster beginnt
```

Robot-Animation, ReSpeaker-Audio-Handling, Hardware-VAD/DoA, Face Tracking, Servos, Status-LED, Deep Sleep, lokale Befehlserkennung und der lokale E-Mail-Pfad bleiben auf dem Raspberry Pi.

Local Mode starten mit:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-local.py
```

Local Mode benötigt keinen OpenAI-API-Schlüssel.

## OpenAI Realtime Cloud Mode

Cloud Mode wird explizit mit `coglet-cloud.py` gestartet. Er öffnet sofort eine kontinuierliche OpenAI-Realtime-Sitzung; es gibt kein Wakeword und keine lokale STT/LLM/TTS-Pipeline für das Gespräch selbst.

Der Raspberry Pi übernimmt weiterhin:

- Mikrofon- und Lautsprecher-Audio,
- ReSpeaker-Hardwareintegration,
- Robot-Animationen und Status-LED,
- Face Tracking und Servo-Steuerung,
- geordnetes Herunterfahren und Servo-Parken,
- lokale SMTP-E-Mail-Zustellung.

OpenAI Realtime übernimmt:

- serverseitige VAD und Gesprächs-Turn-Erkennung,
- Spracherkennung und Reasoning,
- Sprachgenerierung mit einer OpenAI-Stimme,
- Function Calling für unterstützte Coglet-Tools.

Aktuelle Cloud-Mode-Funktionen umfassen:

- kontinuierliche Speech-to-Speech-Konversation mit standardmäßig `gpt-realtime-2`,
- konfigurierbare OpenAI-Stimme und Systemprompt,
- Barge-in und Antwortabbruch,
- geordnetes gesprochenes Herunterfahren, bevor die Servos in ihre Parkposition fahren,
- Function Tool `send_email`: Das Modell erstellt Betreff und strukturierten HTML-Inhalt; der Pi sendet die E-Mail über die vorhandene lokale SMTP-Konfiguration,
- Sitzungsnutzungs-Zusammenfassung beim Shutdown mit Response-Anzahl, Input-/Output-Tokens, gecachten Tokens und Sitzungsdauer.

Cloud Mode mit derselben Umgebungsdatei starten:

```bash
cd /opt/coglet-pi
source .venv/bin/activate
source env-exports.sh
python3 coglet-cloud.py
```

### Cloud-Konfiguration

Beispielwerte in `pi-side/env-exports.sh.example`:

```bash
export OPENAI_API_KEY=""  # set only in the private env-exports.sh
export OPENAI_REALTIME_MODEL="gpt-realtime-2"
export OPENAI_REALTIME_VOICE="marin"
export OPENAI_REALTIME_REASONING_EFFORT="low"
export OPENAI_REALTIME_VAD_MODE="server_vad"
export OPENAI_REALTIME_TRANSCRIPTION="true"
export OPENAI_REALTIME_TRANSCRIPTION_MODEL="gpt-4o-mini-transcribe"
export OPENAI_REALTIME_STARTUP_MESSAGE="Ich bin online und bereit zu helfen."
export OPENAI_REALTIME_SHUTDOWN_MESSAGE="Tschüss!"
```

`coglet-cloud.py` startet immer OpenAI Realtime und bricht klar ab, wenn eine benötigte Konfiguration oder Abhängigkeit fehlt. Es fällt nie auf Local Mode zurück. `coglet-local.py` startet immer Local Mode.

Die Realtime-Persona bzw. der Systemprompt wird getrennt von der lokalen Ollama-Persona geladen. Echte API-Schlüssel dürfen nicht committed werden. Cloud Mode sendet Gesprächsaudio an OpenAI und verursacht API-Nutzungskosten.

Die vollständige gemeinsame Umgebungsvorlage steht in `pi-side/env-exports.sh.example`. Hinweise zur Hardwarevalidierung stehen in `pi-side/MANUAL-HARDWARE-TESTS.md`.

## Lizenz und Credits

Dieses Projekt steht unter der [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-nc-sa/4.0/).

Teile der Servo-Steuerung und Face-Tracking-Logik sind aus Arbeiten von [Will Cogley](https://www.willcogley.com/) adaptiert und werden unter derselben Lizenz (CC BY-NC-SA 4.0) verwendet. Wir haben den Originalcode für das Coglet-Projekt verändert und erweitert.

## Verwendete Hardware

- 1x Raspberry Pi 5 8GB mit USB-C-Netzteil
- 1x Seeedstudio Grove AI Vision V2 mit Kamera
- 1x Seeedstudio ReSpeaker XMOS XVF3800 USB-4-Mic-Array mit Hardware-AEC, AGC, DoA, VAD, Dereverberation, Beamforming und Rauschunterdrückung
- 1x passiver 4-Ohm-, 3-5W-Lautsprecher am ReSpeaker-Lautsprecheranschluss
- 10x MG90S-Mikroservomotoren
- 1x PCA9685-Servotreiber-Board
- 1x Mean Well RSP-100-5, 20A 5V-Netzteil für die Servos
- 1x optionale 5mm NeoPixel-RGB-LED
- 1x Adafruit Pixel Shifter 6066 Pegelwandler
- 3D-gedruckte Coglet-Teile von Will Cogley (https://github.com/will-cogley/Coglet/blob/main/3D%20Printing%20Files/CogletB34Parts.3mf)
- optional ultrarealistische Augäpfel aus Will Cogleys Online-Shop
- 1x Linux-GPU-Server für Local Mode; mindestens 8-12GB VRAM, empfohlen 24/32GB

## Verwendete Open-Source-Software

- Flask
- Faster-Whisper STT (`large-v3-turbo`)
- Ollama mit angepasstem Coglet-Modell
- OpenWakeWord (optionaler Local-Mode-Wakeword-Trigger)
- Mosquitto MQTT
- Piper TTS für Local-Mode-Sprachausgabe
- OpenAI Realtime API über `websocket-client` für Cloud Mode

## Ordnerstruktur

- `pi-side/coglet-local.py` — dedizierter Local-Mode-Launcher
- `pi-side/local_mode.py` — lokale Hardware-VAD-/Wakeword-STT/LLM/TTS-Gesprächsimplementierung
- `pi-side/coglet-cloud.py` — dedizierter kontinuierlicher OpenAI-Realtime-Launcher
- `pi-side/robot_runtime.py` — öffentliche gemeinsame Robot-Hardware-Fassade
- `pi-side/hardware/robot_runtime.py` — Servo-, LED-, Animations- und Tracking-Runtime
- `pi-side/voice_backends/openai_realtime.py` — Realtime-WebSocket-/Audio-Implementierung
- `pi-side/hardware/` — Raspberry-Pi-Hardware-, Servo-, Audio- und Tracking-Module
- `server-side/stt_http_server.py` — Local-Mode-STT-Service
- `server-side/` — lokale STT-, Modell- und Prompt-Assets

![Coglet2](images/Coglet02.jpg)   ![Coglet3](images/Cogletblue1.jpg)  ![Coglet4](images/Cogletblue3.jpg)
