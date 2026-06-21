# Coglet - Technische Übersicht

Dieses Dokument beschreibt den aktuellen Implementierungs-Stack in diesem Repository.

## 1. Architektur auf hoher Ebene

Coglet hat zwei bewusst getrennte Launcher:

| Modus | Launcher | Pipeline |
|---|---|---|
| Local Mode | `pi-side/coglet-local.py` | Hardware-VAD oder optionales Wakeword → Aufnahme → Faster-Whisper STT → Ollama → Piper/MQTT TTS |
| Cloud Mode | `pi-side/coglet-cloud.py` | Kontinuierliche OpenAI-Realtime-Speech-to-Speech-Sitzung |

`coglet-local.py` importiert `local_mode.py` und stellt den lokalen Hardware-VAD-/Wakeword-STT/LLM/TTS-Modus bereit. `coglet-cloud.py` startet OpenAI Realtime direkt und verwendet die lokale Gesprächspipeline nicht.

## 2. Raspberry-Pi-Seite

Der Raspberry Pi besitzt die Echtzeit-Robotik-Laufzeit:

- XVF3800-Hardware-VAD-Sprachtrigger oder optionale OpenWakeWord-Erkennung
- Mikrofonaufnahme und WebRTC-VAD
- lokaler Gesprächszustand und Follow-up-Handling
- Piper/MQTT-Sprachausgabe
- ReSpeaker-Hardwareintegration
- Face Tracking und Servo-Steuerung über die gemeinsame Robot-Runtime
- optionale RGB-Status-LED
- Deep-Sleep- und Aufwachverhalten

## 3. Gesprächspfad im Local Mode

```text
Hardware-VAD-Sprachtrigger oder optionales Wakeword
  -> optionale MODEL_CONFIRM-Ausgabe über Piper, wenn Wakeword-Modus genutzt wird
  -> Audioaufnahme
  -> POST WAV an STT_URL /stt
  -> lokale Befehls-/E-Mail-Erkennung
  -> Ollama /api/chat oder /api/generate
  -> Antwort über Piper/MQTT sprechen
  -> Follow-up-Fenster
```

Der Gesprächsspeicher liegt lokal in `ConversationMemory` innerhalb von `local_mode.py`. Bei einer neuen Wake-/sprachgetriggerten Sitzung löscht `LLM_RESET_ON_WAKE=1` diesen lokalen Speicher.

## 4. Serverseite

`server-side/stt_http_server.py` stellt die Faster-Whisper-Endpunkte `/stt` und `/healthz` bereit. Ollama läuft als normaler Dienst und wird vom Local Mode direkt über `OLLAMA_URL` aufgerufen.

Die Serverseite enthält keine kombinierte STT/LLM/TTS-Pipeline mehr. Die Sprachsynthese erfolgt durch Piper auf der Raspberry-Pi-Seite.

## 5. Cloud Mode

Der optionale Cloud-Launcher verwendet `pi-side/voice_backends/openai_realtime.py` für WebSocket- und Audio-Handling. OpenAI Realtime übernimmt VAD, Spracherkennung, Reasoning und Sprachgenerierung. Coglet besitzt weiterhin lokale Audiogeräte, Robot-Animation, Status-LED, E-Mail-Zustellung und geordnetes Herunterfahren.

## 6. Startup-Checks

Local Mode validiert:

- den Health-Endpunkt von `STT_URL`
- die Verfügbarkeit des konfigurierten `OLLAMA_MODEL` über `OLLAMA_URL`
- Piper/MQTT-Konnektivität

Cloud Mode validiert OpenAI-Realtime-Einstellungen und die Verfügbarkeit von `websocket-client`, ohne kostenpflichtige Realtime-API-Aufrufe auszuführen.

## Mehrsprachige Realtime-Prompts im Cloud Mode

Cloud Mode lädt OpenAI-Realtime-Instruktionen in dieser Reihenfolge: `OPENAI_REALTIME_INSTRUCTIONS`, dann `OPENAI_REALTIME_INSTRUCTIONS_FILE`, dann `pi-side/prompts/openai-realtime-coglet-${COGLET_LANG}.txt`. Deutsch ist der Standard, wenn `COGLET_LANG` nicht gesetzt oder nicht unterstützt ist.
