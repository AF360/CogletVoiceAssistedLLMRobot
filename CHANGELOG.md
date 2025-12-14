# CHANGELOG

## Initial public release
### Added
- Code cleanup
- Servo calibration loader now searches the hardware module directory so start/stop angles from servo-calibration.json are applied at startup and shutdown.
- Status LED driver now uses the Adafruit `neopixel` implementation on GPIO 21 by default to match the validated reference script and avoid initialization errors.
