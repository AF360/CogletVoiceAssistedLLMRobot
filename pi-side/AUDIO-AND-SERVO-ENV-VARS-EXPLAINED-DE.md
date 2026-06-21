| ENV-Variable | Default (`env-exports.sh`) | Im Code verwendet | Wirkung im Code |
| --- | --- | --- | --- |
| Audio-/Recording-bezogene Variablen: |  |  |  |
| 1. Recorder/MIC |  |  |  |
| MIC_SR | "16000" | pi-side/coglet-local.py, Line 148 | Wird als Sample-Rate an den Recorder übergeben: rec = Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR) (coglet-local.py, Line 2193). Der Recorder arbeitet dann exakt mit dieser Rate; diese Rate wird auch an SpeechEndpoint weitergegeben (rec.sr in Lines 2340 and 2408). |
| MIC_DEVICE | "0" (im Recorder) | coglet-local.py, Lines 1483-1484 | Der Recorder liest MIC_DEVICE direkt: dev_env = os.getenv("MIC_DEVICE", "0"). Besteht der Wert nur aus Ziffern, wird er als Index interpretiert, sonst als Gerätename. Steuert damit, welches Eingabegerät sounddevice.RawInputStream verwendet. |
| MIC_GAIN_DB | "0" | coglet-local.py, Line 1485 (Recorder) and Line 149 (globale Konstante) | Im Recorder wird self.gain_db = float(os.getenv("MIC_GAIN_DB", "0")) gesetzt und daraus ein linearer Gain berechnet (self._lin_gain, Line 1491). Beeinflusst die Verstärkung für read() → float32 (Excerpt Lines 1490-1492). |
| MIC_AUTO_GAIN | "0" | coglet-local.py, Line 1486 and Line 150 | Im Recorder: self.auto_gain = os.getenv("MIC_AUTO_GAIN", "0") in ("1", "true", "True"). Dient als Flag für automatische Verstärkung (AGC); die eigentliche AGC-Logik läge in anderen Methoden, ist für Start/Ende der Aufnahme nicht direkt relevant, wird aber geloggt (Lines 1506-1513). |
| MIC_TARGET_DBFS | "-18" | coglet-local.py, Line 1487 and Line 151 | Zielpegel für mögliche AGC: self.target_dbfs = float(os.getenv("MIC_TARGET_DBFS", "-18")) (Line 1487). Derzeit nur im Logging sichtbar (Lines 1506-1513). |
| MIC_MAX_GAIN_DB | "35" | coglet-local.py, Line 1488 and Line 152 | Maximale Software-Verstärkung: self.max_gain_db = float(os.getenv("MIC_MAX_GAIN_DB", "35")). Wird auch im Startlog ausgegeben (Lines 1506-1513). |
|  |  |  |  |
| 2. VAD / Endpointing (SpeechEndpoint) |  |  |  |
| VAD_AGGRESSIVENESS | "2" | coglet-local.py, Line 154 (as VAD_AGGR) | Der Wert wird an den Recorder übergeben (Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR), Line 2193), von dort an SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr) weitergegeben (Lines 2340 and 2408) und schließlich für webrtcvad.Vad(int(vad_aggr)) verwendet (coglet-local.py, Lines 1388-1389). Steuert damit die Empfindlichkeit der WebRTC-VAD. |
| VAD_FRAME_MS | "30" | coglet-local.py, Line 1392 | Setzt die Framelänge in Millisekunden. Die Samples pro Frame werden berechnet als self.frame_samples = (self.sr * self.frame_ms) // 1000, die Bytegröße als self.frame_bytes (Lines 1408-1410). record() liest jedes Mal exakt frame_bytes (recorder.read_bytes(self.frame_bytes), Line 1435). |
| VAD_START_WIN | "5" | coglet-local.py, Line 1393 | Fenstergröße für die Mehrheitsentscheidung: votes = collections.deque(maxlen=self.start_win) (Line 1419). Für Start-VAD werden pro Frame 0/1-Votes in dieses Fenster geschoben (Lines 1441-1445). |
| VAD_START_MIN | "3" | coglet-local.py, Line 1394 | Mindestanzahl an Speech-Votes im Startfenster: if len(votes) == self.start_win and sum(votes) >= self.start_min ... (Lines 1447-1448). |
| VAD_START_CONSEC_MIN | "3" | coglet-local.py, Line 1395 | Mindestanzahl zusammenhängender Speech-Frames: consec_speech = (consec_speech + 1) if is_speech else 0 (Line 1445) und Bedingung ... and consec_speech >= self.start_consec (Line 1447). |
| VAD_END_HANG_MS | "400" | coglet-local.py, Line 1396 | Länge der Hangover-Phase nach Stille in Millisekunden. Daraus wird self.hang_frames = max(1, math.ceil(self.end_hang_ms / self.frame_ms)) gebildet (Line 1411). In End-VAD wird abgebrochen, wenn frames_since_end >= self.hang_frames (Lines 1456-1463). |
| VAD_END_GUARD_MS | "1200" | coglet-local.py, Line 1397 | Mindestdauer ab Speech-Start, bevor ein Ende erlaubt ist: self.end_guard_s = self.end_guard_ms / 1000.0 (Line 1413). In record() wird diese Grenze mit local_end_guard verglichen: if frames_since_end >= self.hang_frames and (now - started_at) >= local_end_guard: (Lines 1460-1463). |
| VAD_PREROLL_MS | "240" | coglet-local.py, Line 1398 | Länge der Preroll-Phase: self.preroll_frames = max(0, self.preroll_ms // self.frame_ms) (Line 1412). Vor Start werden Frames in preroll gespeichert (Lines 1443-1444); bei erkannter Sprache werden diese Frames in den Puffer geschrieben (Lines 1448-1450). |
| MAX_UTTER_S | "8.0" | coglet-local.py, Line 1399 | Harte Obergrenze pro record()-Aufruf. Im Loop: if (now - start_ts) > (self.max_utter if speech_started else local_no_speech): break (Line 1432). Gilt ab Sprachstart (wenn speech_started == True). |
| NO_SPEECH_TIMEOUT_S | "3.0" | coglet-local.py, Line 1400 | Timeout vor erkanntem Sprachstart. Wird zu local_no_speech in record(), wenn kein Argument übergeben wird (Line 1416). Solange speech_started == False gilt: if (now - start_ts) > local_no_speech: break (Line 1432). |
|  |  |  |  |
| 3. Follow-up Mode & TTS-Gating |  |  |  |
| FOLLOWUP_ENABLE | "1" | coglet-local.py, Line 2389 | Wenn os.getenv("FOLLOWUP_ENABLE", "1") in ("1", "true", "True") ist, wird der Follow-up-Block nach jeder Antwort aktiviert. Bei anderen Werten wird der komplette Follow-up-Modus übersprungen. |
| FOLLOWUP_MAX_TURNS | "10" | coglet-local.py, Line 2391 | Wird in max_turns = int(os.getenv("FOLLOWUP_MAX_TURNS", "10")) gelesen. In der Loop-Bedingung: while ... and (max_turns == 0 or turns < max_turns) (Lines 2397-2400). 0 bedeutet unbegrenzt viele Follow-up-Turns, sonst harte Obergrenze. |
| FOLLOWUP_ARM_S | "3.0" | coglet-local.py, Line 2394 | Wird als arm_s gesetzt und direkt an record() übergeben: endpoint.record(rec, no_speech_timeout_s=arm_s) (Line 2411). Damit überschreibt arm_s im Follow-up das Standard-NO_SPEECH_TIMEOUT_S: Wenn innerhalb dieses Zeitfensters kein Sprachstart erkannt wird, bricht record() ab (Lines 1416 and 1432). |
| FOLLOWUP_COOLDOWN_S | "0.10" | coglet-local.py, Line 2395 | Kurze Wartezeit vor jedem Follow-up-Listen: time.sleep(fu_cd) (Line 2403), danach rec.flush() (Line 2404). Dient dazu, TTS-Echo aus dem Puffer zu entfernen. |
| BARGE_IN | Default True | coglet-local.py, Line 135; Auswertung in Lines 1252, 1880-1889 | Wird über _parse_bool(os.getenv("BARGE_IN"), True) gesetzt. Wenn True, bleibt das Mikrofon während TTS aktiv (half_duplex_tts(), Lines 1248-1255). Wenn False, wird _listen während TTS deaktiviert und danach mit zusätzlichem Cooldown und Buffer Flush reaktiviert (speak_and_back_to_idle, Lines 1880-1895). Beeinflusst damit, ob Audio während Sprachausgabe in die Pipelines gelangt. |
| COOLDOWN_AFTER_TTS_S | "0.5" | coglet-local.py, Line 1890 | Nur wirksam, wenn BARGE_IN False ist. Nach TTS wird post_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5")) gesetzt und, falls > 0, per time.sleep(post_cd) gewartet (Lines 1890-1892). Danach werden Eingabepuffer geleert (_flush_input_buffers(recorder)) und das Wakeword neu scharfgestellt (kw.reset_after_tts(), Lines 1893-1894). |
|  |  |  |  |
| Servo-bezogene Variablen: |  |  |  |
| 1. Face-Tracking-Hauptschalter & Grove Vision AI UART |  |  |  |
| FACE_TRACKING_ENABLED | "1" | coglet-local.py, lines 195, 539-540, 2106, 2210 | Globaler Ein-/Ausschalter für Face Tracking. Bei 0 gibt _create_face_tracker() None zurück und Tracking ist deaktiviert. |
| FACE_TRACKING_SERIAL_PORT | "/dev/ttyACM0" | coglet-local.py, line 554 | UART-Gerät, das GroveVisionAIClient verwendet. |
| FACE_TRACKING_BAUDRATE | "921600" | coglet-local.py, line 555 | Baudrate, die an den GroveVisionAIClient-Konstruktor übergeben wird. |
| FACE_TRACKING_SERIAL_TIMEOUT | "0.0" | coglet-local.py, line 556 | Lese-Timeout in Sekunden für den Grove Vision AI Client. |
|  |  |  |  |
| 2. PCA9685-Frequenz & Servo-Kanalauswahl |  |  |  |
| FACE_TRACKING_PWM_FREQ_HZ | "50.0" | coglet-local.py, line 349 | PWM-Frequenz (Hz), die auf alle für Face Tracking verwendeten Servos angewendet wird. |
| FACE_TRACKING_EYE_CHANNELS | "0,1" | coglet-local.py, lines 351-359 | Basisliste der PCA9685-Kanäle für Augen (EYL, EYR). Geparst über resolve_channel_list. |
| FACE_TRACKING_WHEEL_CHANNELS | "8,9" | coglet-local.py, lines 361-363, 386-395 | Basisliste der PCA9685-Kanäle für Räder (LWH, RWH). Leere Liste deaktiviert Rad-Tracking. |
| FACE_TRACKING_EYE_LEFT_CHANNEL | "0" | coglet-local.py, lines 397-399 | Überschreibt Kanal für EYL (linkes Auge). |
| FACE_TRACKING_EYE_RIGHT_CHANNEL | "1" | coglet-local.py, lines 400-402 | Überschreibt Kanal für EYR (rechtes Auge). |
| FACE_TRACKING_YAW_CHANNEL | "" (leer) | coglet-local.py, lines 369-411 | Optionale Kanäle für NRL (Yaw). Leer bedeutet, dass der Yaw-Servo für Tracking deaktiviert ist. |
| FACE_TRACKING_PITCH_CHANNEL | "3" | coglet-local.py, lines 369-417 | Optionale Kanäle für NPT (Pitch). Leer bedeutet, dass der Pitch-Servo für Tracking deaktiviert ist. |
| FACE_TRACKING_WHEEL_LEFT_CHANNEL | "8" | coglet-local.py, lines 419-421 | Überschreibt Kanal für LWH (linkes Rad). |
| FACE_TRACKING_WHEEL_RIGHT_CHANNEL | "9" | coglet-local.py, lines 422-423 | Überschreibt Kanal für RWH (rechtes Rad). |
|  |  |  |  |
| 3. Face-Tracking-Geometrie & Controller-Gains |  |  |  |
| FACE_TRACKING_FRAME_WIDTH | "220.0" | FaceTrackingConfig.frame_width, line 71; _build_face_tracking_config, line 267 | Logische Framebreite zur Berechnung des Zentrums (frame_center_x). |
| FACE_TRACKING_FRAME_HEIGHT | "200.0" | FaceTrackingConfig.frame_height, line 72; _build_face_tracking_config, line 268 | Logische Framehöhe zur Berechnung von frame_center_y. |
| FACE_TRACKING_COORDINATES_CENTER | "1" (true) | FaceTrackingConfig.coordinates_are_center, line 73; _build_face_tracking_config, line 269; _extract_center(), lines 229-232 | Wenn true, nutzt Tracking box.x, box.y als Zentrum; wenn false, box.center_x, box.center_y. |
| FACE_TRACKING_EYE_DEADZONE_PX | "10.0" | FaceTrackingConfig.eye_deadzone_px, line 74; _build_face_tracking_config, line 270; _handle_detection(), lines 175-192 | Horizontaler Pixel-Schwellenwert, unter dem Augenbewegung unterdrückt wird. |
| FACE_TRACKING_YAW_DEADZONE_PX | not in env-exports → default 18.0 | FaceTrackingConfig.yaw_deadzone_px, line 75; _build_face_tracking_config, line 271 | Derzeit in der FaceTracker-Logik nicht genutzt; für Symmetrie vorhanden. |
| FACE_TRACKING_PITCH_DEADZONE_PX | "18.0" | FaceTrackingConfig.pitch_deadzone_px, line 76; _build_face_tracking_config, line 272 | Vertikale Pixel-Deadzone, bevor Pitch angepasst wird. |
| FACE_TRACKING_EYE_GAIN_DEG_PER_PX | "0.08" | FaceTrackingConfig.eye_gain_deg_per_px, line 76-80; _build_face_tracking_config, line 273 | Grad Augenrotation pro Pixel horizontalem Fehler. |
| FACE_TRACKING_YAW_GAIN_DEG_PER_PX | not in env-exports → default 0.05 | FaceTrackingConfig.yaw_gain_deg_per_px, line 78; _build_face_tracking_config, line 274 | Gain für Yaw, analog zu Eye Gain. |
| FACE_TRACKING_PITCH_GAIN_DEG_PER_PX | "0.06" (sign flipped vs comment) | FaceTrackingConfig.pitch_gain_deg_per_px, line 79-80; _build_face_tracking_config, line 275; comment in env-exports.sh lines 128-130 | Vertikaler Gain; Kommentar erwähnt Vorzeichenänderung, weil Pitch-Richtung invertiert war. |
| FACE_TRACKING_EYE_MAX_DELTA_DEG | "20.0" | FaceTrackingConfig.eye_max_delta_deg, line 81; _build_face_tracking_config, line 276; _clamp(), lines 235-236; _handle_detection(), lines 193-201 | Per-Update-Klemme für Änderung des Augen-Zielwinkels in Grad. |
| FACE_TRACKING_YAW_MAX_DELTA_DEG | not in env-exports → 30.0 | FaceTrackingConfig.yaw_max_delta_deg, line 81-82; _build_face_tracking_config, line 277 | Per-Update-Klemme für Yaw-Zieländerungen (im aktuellen Loop nicht aktiv genutzt). |
| FACE_TRACKING_PITCH_MAX_DELTA_DEG | "20.0" | FaceTrackingConfig.pitch_max_delta_deg, line 82; _build_face_tracking_config, line 278; _handle_detection(), lines 193-201 | Per-Update-Klemme für Pitch-Änderungen. |
| FACE_TRACKING_INVOKE_INTERVAL_S | "0.05" | FaceTrackingConfig.invoke_interval_s, line 83; _build_face_tracking_config, line 279; _run(), lines 145-163 | Mindestzeit zwischen aufeinanderfolgenden GroveVisionAIClient.invoke_once()-Aufrufen. |
| FACE_TRACKING_INVOKE_TIMEOUT_S | "0.25" | FaceTrackingConfig.invoke_timeout_s, line 84; _build_face_tracking_config, line 280; _run(), lines 155-160 | Timeout, der an invoke_once(timeout=…) übergeben wird. |
| FACE_TRACKING_UPDATE_INTERVAL_S | "0.01" | FaceTrackingConfig.update_interval_s, line 85; _build_face_tracking_config, line 281; _run(), line 163 | Schlafdauer zwischen Loop-Iterationen; höher = langsamere Servo-Updates. |
| FACE_TRACKING_NEUTRAL_TIMEOUT_S | "2.0" | FaceTrackingConfig.neutral_timeout_s, line 86; _build_face_tracking_config, line 282; _handle_missing_detection(), lines 213-220 | Zeit ohne Erkennung, nach der alle Tracking-Servos zurück auf Neutralwinkel gefahren werden. |
|  |  |  |  |
| 4. Wheel-follow behaviour (Basisrotation aus Augenabweichung) |  |  |  |
| FACE_TRACKING_WHEEL_DEADZONE_DEG | "5.0" | FaceTrackingConfig.wheel_deadzone_deg, line 87; _build_face_tracking_config, line 283; _update_wheel_follow(), lines 246-255 | Minimale Augenabweichung (in Grad) von Neutral, bevor Räder anfangen zu drehen. |
| FACE_TRACKING_WHEEL_FOLLOW_DELAY_S | "0.8" | FaceTrackingConfig.wheel_follow_delay_s, line 88; _build_face_tracking_config, line 284; _update_wheel_follow(), lines 257-260 | Verzögerung zwischen Überschreiten des Augenabweichungsschwellenwerts und Beginn der Radfolgebewegung. |
| FACE_TRACKING_WHEEL_INPUT_MIN_DEG | "30.0" | FaceTrackingConfig.wheel_input_min_deg, line 89; _build_face_tracking_config, line 285; _map_eye_to_wheel_target(), lines 276-292 | Untere Grenze des Augenwinkels für Wheel Mapping. |
| FACE_TRACKING_WHEEL_INPUT_MAX_DEG | "150.0" | FaceTrackingConfig.wheel_input_max_deg, line 90; _build_face_tracking_config, line 286 | Obere Grenze des Augenwinkels für Wheel Mapping. |
| FACE_TRACKING_WHEEL_OUTPUT_MIN_DEG | "80.0" | FaceTrackingConfig.wheel_output_min_deg, line 91; _build_face_tracking_config, line 287 | Minimaler Wheel-Zielwinkel im Mapping. |
| FACE_TRACKING_WHEEL_OUTPUT_MAX_DEG | "100.0" | FaceTrackingConfig.wheel_output_max_deg, line 92; _build_face_tracking_config, line 288 | Maximaler Wheel-Zielwinkel im Mapping. |
| FACE_TRACKING_WHEEL_POWER | "2.0" | FaceTrackingConfig.wheel_power, line 93; _build_face_tracking_config, line 289; _remap_curved(), lines 293-304 | Exponent, der das nichtlineare Mapping von Augenabweichung zu Radwinkel steuert. |
|  |  |  |  |
| 5. Servo-Tuning-Overrides für Tracking-Servos |  |  |  |
| prefix_map = { |  |  |  |
|     "EYL": "FACE_TRACKING_EYE", |  |  |  |
|     "EYR": "FACE_TRACKING_EYE", |  |  |  |
|     "NPT": "FACE_TRACKING_PITCH", |  |  |  |
|     "NRL": "FACE_TRACKING_YAW", |  |  |  |
|     "LWH": "FACE_TRACKING_WHEEL_LEFT", |  |  |  |
|     "RWH": "FACE_TRACKING_WHEEL_RIGHT", |  |  |  |
| } |  |  |  |
| Für jedes Prefix P in der folgenden Tabelle werden optional diese Env Vars gelesen: |  |  |  |
| P_MIN_ANGLE_DEG |  | coglet-local.py, lines 231-244 | Wenn eine spezifische Env Var fehlt oder ungültig ist, wird stattdessen der entsprechende Wert aus dem Basislayout (SERVO_LAYOUT_V1) verwendet. |
| P_MAX_ANGLE_DEG |  |  |  |
| P_MIN_PULSE_US |  |  |  |
| P_MAX_PULSE_US |  |  |  |
| P_MAX_SPEED_DEG_PER_S |  |  |  |
| P_MAX_ACCEL_DEG_PER_S2 |  |  |  |
| P_DEADZONE_DEG |  |  |  |
| P_NEUTRAL_DEG |  |  |  |
| P_INVERT |  |  |  |
| P_PWM_FREQ_HZ |  |  |  |
|  |  |  |  |
| FACE_TRACKING_EYE_MIN_ANGLE_DEG | not exported | servo_presets.py, lines 95-136 | nicht exportiert (Basis aus SERVO_LAYOUT_V1["EYL"/"EYR"].config.min_angle_deg). |
| FACE_TRACKING_EYE_MAX_ANGLE_DEG | "150.0" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_EYE_MIN_PULSE_US | "600.0" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_EYE_MAX_PULSE_US | "2400.0" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_EYE_MAX_SPEED_DEG_PER_S | "200.0" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_EYE_MAX_ACCEL_DEG_PER_S2 | "1000.0" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_EYE_DEADZONE_DEG | "0.8" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_EYE_NEUTRAL_DEG | "90" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_EYE_INVERT | "0" | env-exports.sh, lines 149-156 | Eye-Servo-Overrides |
| FACE_TRACKING_PITCH_MIN_ANGLE_DEG | "1.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_MAX_ANGLE_DEG | "120.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_MIN_PULSE_US | "600.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_MAX_PULSE_US | "2400.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_MAX_SPEED_DEG_PER_S | "600.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_MAX_ACCEL_DEG_PER_S2 | "400.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_DEADZONE_DEG | "1.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_NEUTRAL_DEG | "50.0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_PITCH_INVERT | "0" | env-exports.sh, lines 158-166 | Pitch-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_MIN_ANGLE_DEG | "30." | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_ANGLE_DEG | "120.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_MIN_PULSE_US | "600.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_PULSE_US | "2400.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_SPEED_DEG_PER_S | "100.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_ACCEL_DEG_PER_S2 | "25.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_DEADZONE_DEG | "1.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_NEUTRAL_DEG | "90.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_LEFT_INVERT | "0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_MIN_ANGLE_DEG | "30.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_ANGLE_DEG | "120..0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_MIN_PULSE_US | "600.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_PULSE_US | "2400.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_SPEED_DEG_PER_S | "100.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_ACCEL_DEG_PER_S2 | "25.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_DEADZONE_DEG | "1.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_NEUTRAL_DEG | "90.0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
| FACE_TRACKING_WHEEL_RIGHT_INVERT | "0" | env-exports.sh, lines 168-186 | Wheel-Servo-Overrides |
|  |  |  |  |
| 6. Augenlid-/Blinzel-Controller |  |  |  |
| EYELID_OPEN_DEG | layout_cfg.neutral_deg (neutraler LID-Winkel) | coglet-local.py, lines 298-300 | Absoluter Winkel für „Augen offen“. Zwischen Servo-Min/Max geklemmt. |
| EYELID_CLOSED_DEG | open_angle - 60.0 (zwischen Servo-Min/Max geklemmt) | coglet-local.py, lines 300-303 | Absoluter Winkel für „Augen geschlossen“. |
| EYELID_SLEEP_FRACTION | 0.7 (auf 0.0-1.0 geklemmt) | coglet-local.py, lines 304-305 | Anteil zwischen offen/geschlossen für den schläfrigen halbgeschlossenen Zustand. |
| EYELID_BLINK_MIN_S | 3.0 | coglet-local.py, line 306 | Minimales Zufallsintervall zwischen Blinks. |
| EYELID_BLINK_MAX_S | 7.0 | coglet-local.py, line 307 | Maximales Zufallsintervall zwischen Blinks. |
| EYELID_BLINK_CLOSE_S | 0.06 | coglet-local.py, line 308 | Dauer der Schließphase eines Blinks. |
| EYELID_BLINK_HOLD_S | 0.04 | coglet-local.py, line 309 | Haltezeit mit geschlossenen Augen. |
| EYELID_BLINK_OPEN_S | 0.07 | coglet-local.py, line 310 | Dauer der Öffnungsphase eines Blinks. |
|  |  |  |  |
| 7. Generische Animation-Servo-Overrides |  |  |  |
| Für Nicht-Tracking-Servos (z. B. Mund, Ohren, Head Roll usw.) verwendet _create_servo_setup() prefix = f"ANIM_{name}" für jeden Servo-Namen, der nicht explizit in prefix_map aufgeführt ist (coglet-local.py, lines 472-490). |  |  |  |
| Das bedeutet, dass mechanische Limits und Dynamik mit demselben Muster wie oben gesteuert werden können, aber mit dem Prefix ANIM_, z. B.: |  |  |  |
| ANIM_MOU_MIN_ANGLE_DEG |  |  |  |
| ANIM_MOU_MAX_ANGLE_DEG |  |  |  |
| ANIM_MOU_MAX_SPEED_DEG_PER_S |  |  |  |
| ANIM_EAR_NEUTRAL_DEG |  |  |  |
| ANIM_EAR_INVERT |  |  |  |
| Alle diese Namen werden ausschließlich über _create_servo_config() aufgelöst (coglet-local.py, lines 217-244) und fallen auf die entsprechenden SERVO_LAYOUT_V1-Defaults in servo_presets.py zurück, wenn sie nicht gesetzt sind. |  |  |  |
