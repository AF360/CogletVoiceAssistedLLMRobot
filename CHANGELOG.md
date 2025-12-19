# CHANGELOG

## Initial public release
### Added
- Code cleanup
- Servo calibration loader now searches the hardware module directory so start/stop angles from servo-calibration.json are applied at startup and shutdown.
- Status LED driver now uses the Adafruit `neopixel` implementation on GPIO 21 by default to match the validated reference script and avoid initialization errors.

## [v1.0.5.3] - 2025-12-19
### Added
- **Multi-Language Support:** Global language switch (`COGLET_LANG`) for German (`de`) and English (`en`).
  - Automatically adjusts TTS voice, STT language parameter, and system prompts.
  - Dynamically adapts the LLM persona (friendly German assistant vs. friendly English assistant).
- **Agentic Capabilities (Email):** Coglet can now send emails.
  - Detects intents like "Send me a recipe for a great burger sauce via email".
  - Uses `smtplib` for delivery (configured via ENV).
  - Generates professionally formatted HTML content using the LLM.
- **Turn-to-Voice (Body Orientation):**
  - Coglet physically turns its body (via wheel servos) towards the speaker.
  - Based on DOA (Direction of Arrival) from the ReSpeaker Mic Array.
  - This behaviour is optional and can be controlled by a config-switches 
- **STT Server:** Now supports dynamic prompts (`WHISPER_PROMPT_DE`/`_EN`) to prevent hallucinations or unwanted translation when switching languages.
- **Config:** Cleaned up `env-exports.sh`; hardcoded strings replaced by internal dictionary lookups.
