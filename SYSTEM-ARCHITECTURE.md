# Coglet – Hybrid architecture & ports

**STT (Whisper) + LLM (Ollama) on the PC**
**OpenWakeword, facetracking, servo-control, TTS (Piper) locally on the Raspberry Pi**.

```
┌──────────────────────────────┐                          ┌──────────────────────────────┐
│ Raspberry Pi 4/5             │                          │ PC / Mac                     │
│──────────────────────────────│                          │──────────────────────────────│
│ • Mikrophone (ALSA)          │                          │ • STT-HTTP-Server (Flask)    │
│ • Speaker (ALSA)             │                          │   - faster-whisper Model     │
│ • Client: coglet-pi.py       │                          │     (large-v3 / -turbo)      │
│   - keeps track of states    │                          │ • Ollama Daemon (/api/chat)  │
│ • TTS: Piper over MQTT       │                          │   - cutomised Qwen2.5:7b LLM │
│ • Facetracking               │                          │                              │
│ • Servo controls             │                          │                              │
│ • optional LED               │                          │                              │
└──────────────┬───────────────┘                          └──────────────┬───────────────┘
               │                                                        │
   HTTP POST   │  WAV → /stt (lang=xx)                                  │  Port 5005/TCP
─────────────▶│───────────────────────────────────────────────────────▶│  (incoming)
  JSON-Reply   │   { "text": "…" }                                      │
◀─────────────│───────────────────────────────────────────────────────◀│
   HTTP POST   │  Chat → /api/chat (messages[..])                       │  Port 11434/TCP
──────────────▶│──────────────────────────────────────────────────────▶│  (incoming)
  JSON-Reply   │   { "message": { "content": "…" } }                    │
◀──────────────│──────────────────────────────────────────────────────◀│
               │
        (local Piper-TTS, audio-output, facetracking, servo-animations)
```

### Ports & Protocols
| Direction                | Srouce (Host) | Dest (Host) | Port | Protocol  | Purpose                        | Config-variables    |
|--------------------------|---------------|-------------|------|-----------|--------------------------------|---------------------|
| Pi → PC                  | Raspberry Pi  | PC          | 5005 | HTTP      | Speech-to-Text (`/stt`)        | `STT_HTTP_URL`      |
| Pi → PC                  | Raspberry Pi  | PC          | 11434| HTTP      | LLM Chat (`/api/chat`)         | `OLLAMA_URL`        |
| Pi → Pi                  | Raspberry Pi  | Raspberry Pi| FIFO |           | Text-to-Speech (`/tts`)        | `PIPER_URL`         |
| Pi ↔ PCA9685 (lokal)     | Raspberry Pi  | PCA9685     | n/a  | I2C       | Servo-Kommandos                | `I2C-Bus (SDA/SCL)` |

**Firewalls:** **TCP 5005** (STT-server) and **TCP 11434** (Ollama-server) need to be accessable from the **Raspberry Pi**.

## Relevant env-variables (not the complete list)

**Client (Pi):**
```
STT_REMOTE=off|http
STT_HTTP_URL=http://<PC-IP>:5005/stt
OLLAMA_URL=http://<PC-IP>:11434
COGLET_TTS=pyttsx3|piper
COGLET_SERIAL_PORT=/dev/ttyACM0
```

**STT-Server (PC/Mac):**
```
STT_HTTP_PORT=5005
WHISPER_MODEL=large-v3-turbo   # oder large-v3
WHISPER_DEVICE=auto            # cuda|cpu|auto
```

**Ollama (PC/Mac):**
```
# PC/Mac im LAN freigeben
OLLAMA_HOST=0.0.0.0:11434
```

**Licensing and Credits**

Coglet is distributed under the
[Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License (CC BY-NC-SA 4.0)](https://creativecommons.org/licenses/by-nc-sa/4.0/).

Parts of the servo and face-tracking subsystem are adapted from reference code by
[Will Cogley](https://www.willcogley.com/), used under the same license:

- **Servo layout and pose presets** in `pi-side/hardware/servo_presets.py` are based on
  the original servo mapping and neutral/limit angles for eyes, lid, and head pitch/yaw.
- **Face-tracking logic** in `pi-side/hardware/face_tracker.py` is partly derived from
  Will Cogley’s face-tracking mini bot, in particular:
  - the error-centric tracking loop for eyes and head pitch with deadzones, and
  - the delayed base/body rotation once the eye offset passes a threshold.
- The non-linear **power mapping helper** (`_power_map` in `face_tracker.py`) follows
  the same exponent-curve approach as in the original reference implementation.

All of these components have been refactored and extended for the Coglet project, but
the conceptual foundations and initial implementations are credited to Will Cogley.

### Commercial use:

This project is released under CC BY-NC-SA 4.0 and includes components
derived from reference code by Will Cogley.

- For any commercial use of the parts derived from Will Cogley’s work,
  please contact **enquiries@willcogley.com**.
- For commercial licensing inquiries regarding the Coglet-specific
  additions and surrounding code, please contact **enquiries@acelab.net**.

Any commercial use of the combined work must respect both the original
license terms and the permissions of the respective authors.



**Noteworthy mentions**
- **Echo-Cancellation for Barge-In**
- **Latency** low latency solution
- **Privacy**: all data is processed fully locally without cloud
