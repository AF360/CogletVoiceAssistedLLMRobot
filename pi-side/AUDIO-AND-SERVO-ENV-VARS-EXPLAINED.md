| ENV Variable | Default (env-exports.sh) | Used in the Code | Effect in Code |
| --- | --- | --- | --- |
| Audio/Recording related variables: |  |  |  |
| 1. Recorder/MIC |  |  |  |
| MIC_SR | "16000" | pi-side/hardware/audio.py | Is passed as Sample-Rate to the recorder: rec = Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR). The recorder then works with exactly this rate; this rate is also passed on to SpeechEndpoint. |
| MIC_DEVICE | "0" (in the Recorder) | pi-side/hardware/audio.py | The recorder reads MIC_DEVICE directly: dev_env = os.getenv("MIC_DEVICE", "0"). If the value consists only of digits, it is interpreted as an index, otherwise as a device name. Thus controls which input device sounddevice.RawInputStream uses. |
| MIC_GAIN_DB | "0" | pi-side/hardware/audio.py | In the recorder, self.gain_db = float(os.getenv("MIC_GAIN_DB", "0")) is set, and a linear gain is calculated from it (self._lin_gain). Affects the amplification for read() → float32. |
| MIC_AUTO_GAIN | "0" | pi-side/hardware/audio.py | In the recorder: self.auto_gain = os.getenv("MIC_AUTO_GAIN", "0") in ("1", "true", "True"). Serves as a flag for automatic gain control (AGC); the actual AGC logic would be in other methods (not directly relevant for start/end of recording), but is at least logged. |
| MIC_TARGET_DBFS | "-18" | pi-side/hardware/audio.py | Target level for a possible AGC: self.target_dbfs = float(os.getenv("MIC_TARGET_DBFS", "-18")). Currently only visible in logging. |
| MIC_MAX_GAIN_DB | "35" | pi-side/hardware/audio.py | Maximum software gain: self.max_gain_db = float(os.getenv("MIC_MAX_GAIN_DB", "35")). Is also output in the start log. |
|  |  |  |  |
| 2. VAD / Endpointing (SpeechEndpoint) |  |  |  |
| VAD_AGGRESSIVENESS | "2" | pi-side/hardware/audio.py | The value is passed to the recorder (Recorder(sr=MIC_SR, vad_aggr=VAD_AGGR), passed through from there to SpeechEndpoint(sr=rec.sr, vad_aggr=rec.vad_aggr) and finally used for webrtcvad.Vad(int(vad_aggr)). Thus controls the sensitivity of the WebRTC-VAD. |
| VAD_FRAME_MS | "30" | pi-side/hardware/audio.py | Sets the frame length in milliseconds. The number of samples per frame is calculated as self.frame_samples = (self.sr * self.frame_ms) // 1000, the byte size as self.frame_bytes. record() reads exactly frame_bytes each time (recorder.read_bytes(self.frame_bytes)). |
| VAD_START_WIN | "5" | pi-side/hardware/audio.py | Window size for the majority decision: votes = collections.deque(maxlen=self.start_win). For Start-VAD, 0/1 votes are pushed into this window per frame. |
| VAD_START_MIN | "3" | pi-side/hardware/audio.py | Minimum number of Speech votes in the start window: if len(votes) == self.start_win and sum(votes) >= self.start_min ... |
| VAD_START_CONSEC_MIN | "3" | pi-side/hardware/audio.py | Minimum number of consecutive speech frames: consec_speech = (consec_speech + 1) if is_speech else 0 and condition ... and consec_speech >= self.start_consec. |
| VAD_END_HANG_MS | "400" | pi-side/hardware/audio.py | Length of the "hangover" phase after silence in milliseconds. From this, self.hang_frames = max(1, math.ceil(self.end_hang_ms / self.frame_ms)) is formed. In End-VAD, the process is aborted when frames_since_end >= self.hang_frames. |
| VAD_END_GUARD_MS | "1200" | pi-side/hardware/audio.py | Minimum duration from speech start until allowed ending: self.end_guard_s = self.end_guard_ms / 1000.0. In record(), this limit is compared with local_end_guard: if frames_since_end >= self.hang_frames and (now - started_at) >= local_end_guard. |
| VAD_PREROLL_MS | "240" | pi-side/hardware/audio.py | Length of the preroll phase: self.preroll_frames = max(0, self.preroll_ms // self.frame_ms). Before start, frames are saved in preroll; when start is detected, these frames are also written to the buffer. |
| MAX_UTTER_S | "8.0" | pi-side/hardware/audio.py | Hard upper limit per record() call. In the loop: if (now - start_ts) > (self.max_utter if speech_started else local_no_speech): break. Applies from speech start (when speech_started == True). |
| NO_SPEECH_TIMEOUT_S | "3.0" | pi-side/hardware/audio.py | Timeout before detected speech start. Becomes local_no_speech in record() if no argument is passed. As long as speech_started == False is true: if (now - start_ts) > local_no_speech: break. |
|  |  |  |  |
| 3. Follow-up Mode & TTS Gating |  |  |  |
| FOLLOWUP_ENABLE | "1" | pi-side/coglet-pi.py | If os.getenv("FOLLOWUP_ENABLE", "1") is in ("1", "true", "True"), the follow-up block is activated after every response. For other values, the entire follow-up mode is skipped. |
| FOLLOWUP_MAX_TURNS | "10" | pi-side/coglet-pi.py | Is read into max_turns = int(os.getenv("FOLLOWUP_MAX_TURNS", "10")). In the loop condition: while ... and (max_turns == 0 or turns < max_turns). 0 means an unlimited number of follow-up turns, otherwise a hard upper limit. |
| FOLLOWUP_ARM_S | "3.0" | pi-side/coglet-pi.py | Is set as arm_s and then passed directly to record(): endpoint.record(rec, no_speech_timeout_s=arm_s). Thus, arm_s in the follow-up overwrites the standard NO_SPEECH_TIMEOUT_S: if no speech start is detected within this time window, record() aborts. |
| FOLLOWUP_COOLDOWN_S | "0.10" | pi-side/coglet-pi.py | Short waiting time before each follow-up listen: time.sleep(fu_cd), followed by rec.flush(). Serves to remove TTS echo from the buffer before listening again. |
| BARGE_IN | Default True | pi-side/coglet-pi.py | Is set via _parse_bool(os.getenv("BARGE_IN"), True). If True, the microphone remains active during TTS (half_duplex_tts()). If False, _listen is deactivated during TTS and reactivated after TTS with additional cooldown and buffer flush (speak_and_back_to_idle). Thus influences whether audio enters the pipelines during speech output. |
| COOLDOWN_AFTER_TTS_S | "0.5" | pi-side/coglet-pi.py | Only effective if BARGE_IN is False. After TTS, post_cd = float(os.getenv("COOLDOWN_AFTER_TTS_S", "0.5")) is set and – if > 0 – waited via time.sleep(post_cd). Subsequently, input buffers are cleared (_flush_input_buffers(recorder)) and the wakeword is newly "armed" (activated) (kw.reset_after_tts()). |
|  |  |  |  |
| Servo-related variables: |  |  |  |
| 1. Face tracking master switch & Grove Vision AI UART |  |  |  |
| FACE_TRACKING_ENABLED | "1" | coglet-pi.py | Global on/off switch for face tracking. If set to 0, _create_face_tracker() returns None and tracking is disabled. |
| FACE_TRACKING_SERIAL_PORT | "/dev/ttyACM0" | coglet-pi.py | UART device used by GroveVisionAIClient. |
| FACE_TRACKING_BAUDRATE | "921600" | coglet-pi.py | Baud rate passed to GroveVisionAIClient constructor. |
| FACE_TRACKING_SERIAL_TIMEOUT | "0.0" | coglet-pi.py | Read timeout (seconds) for the Grove Vision AI client. |
|  |  |  |  |
| 2. PCA9685 frequency & servo channel selection |  |  |  |
| FACE_TRACKING_PWM_FREQ_HZ | "50.0" | coglet-pi.py | PWM frequency (Hz) applied to all servos used for face tracking. |
| FACE_TRACKING_EYE_CHANNELS | "0,1" | coglet-pi.py | Base list of PCA9685 channels for eyes (EYL, EYR). Parsed via resolve_channel_list. |
| FACE_TRACKING_WHEEL_CHANNELS | "8,9" | coglet-pi.py | Base list of PCA9685 channels for wheels (LWH, RWH). Empty list disables wheel tracking. |
| FACE_TRACKING_EYE_LEFT_CHANNEL | "0" | coglet-pi.py | Overrides channel for EYL (left eye). |
| FACE_TRACKING_EYE_RIGHT_CHANNEL | "1" | coglet-pi.py | Overrides channel for EYR (right eye). |
| FACE_TRACKING_YAW_CHANNEL | "" (empty) | coglet-pi.py | Optional channel(s) for NRL (yaw). Empty means yaw servo is disabled for tracking. |
| FACE_TRACKING_PITCH_CHANNEL | "3" | coglet-pi.py | Optional channel(s) for NPT (pitch). Empty means pitch servo is disabled for tracking. |
| FACE_TRACKING_WHEEL_LEFT_CHANNEL | "8" | coglet-pi.py | Overrides channel for LWH (left wheel). |
| FACE_TRACKING_WHEEL_RIGHT_CHANNEL | "9" | coglet-pi.py | Overrides channel for RWH (right wheel). |
|  |  |  |  |
| 3. Face tracking geometry & controller gains |  |  |  |
| FACE_TRACKING_FRAME_WIDTH | "220.0" | FaceTrackingConfig.frame_width, _build_face_tracking_config | Logical frame width used to compute the center (frame_center_x). |
| FACE_TRACKING_FRAME_HEIGHT | "200.0" | FaceTrackingConfig.frame_height, _build_face_tracking_config | Logical frame height used to compute frame_center_y. |
| FACE_TRACKING_COORDINATES_CENTER | "1" (true) | FaceTrackingConfig.coordinates_are_center, _build_face_tracking_config, _extract_center() | If true, tracking uses box.x, box.y as center; if false, uses box.center_x, box.center_y. |
| FACE_TRACKING_EYE_DEADZONE_PX | "10.0" | FaceTrackingConfig.eye_deadzone_px, _build_face_tracking_config, _handle_detection() | Horizontal pixel threshold below which eye movement is suppressed. |
| FACE_TRACKING_YAW_DEADZONE_PX | not in env-exports → default 18.0 | FaceTrackingConfig.yaw_deadzone_px, _build_face_tracking_config | Currently unused in FaceTracker logic; present for symmetry. |
| FACE_TRACKING_PITCH_DEADZONE_PX | "18.0" | FaceTrackingConfig.pitch_deadzone_px, _build_face_tracking_config | Vertical pixel deadzone before pitch is adjusted. |
| FACE_TRACKING_EYE_GAIN_DEG_PER_PX | "0.08" | FaceTrackingConfig.eye_gain_deg_per_px, _build_face_tracking_config | Degrees of eye rotation per pixel of horizontal error. |
| FACE_TRACKING_YAW_GAIN_DEG_PER_PX | not in env-exports → default 0.05 | FaceTrackingConfig.yaw_gain_deg_per_px, _build_face_tracking_config | Gain for yaw, analogous to eye gain. |
| FACE_TRACKING_PITCH_GAIN_DEG_PER_PX | "0.06" (sign flipped vs comment) | FaceTrackingConfig.pitch_gain_deg_per_px, _build_face_tracking_config | Vertical gain; comment notes sign change because pitch direction was inverted. |
| FACE_TRACKING_EYE_MAX_DELTA_DEG | "20.0" | FaceTrackingConfig.eye_max_delta_deg, _build_face_tracking_config, _clamp(), _handle_detection() | Per-update clamp for eye target change in degrees. |
| FACE_TRACKING_YAW_MAX_DELTA_DEG | not in env-exports → 30.0 | FaceTrackingConfig.yaw_max_delta_deg, _build_face_tracking_config | Per-update clamp for yaw target changes (not actively used in current loop). |
| FACE_TRACKING_PITCH_MAX_DELTA_DEG | "20.0" | FaceTrackingConfig.pitch_max_delta_deg, _build_face_tracking_config, _handle_detection() | Per-update clamp for pitch changes. |
| FACE_TRACKING_INVOKE_INTERVAL_S | "0.05" | FaceTrackingConfig.invoke_interval_s, _build_face_tracking_config, _run() | Minimum time between consecutive GroveVisionAIClient.invoke_once() calls. |
| FACE_TRACKING_INVOKE_TIMEOUT_S | "0.25" | FaceTrackingConfig.invoke_timeout_s, _build_face_tracking_config, _run() | Timeout passed into invoke_once(timeout=…). |
| FACE_TRACKING_UPDATE_INTERVAL_S | "0.01" | FaceTrackingConfig.update_interval_s, _build_face_tracking_config, _run() | Sleep duration between loop iterations; higher = slower servo updates. |
| FACE_TRACKING_NEUTRAL_TIMEOUT_S | "2.0" | FaceTrackingConfig.neutral_timeout_s, _build_face_tracking_config, _handle_missing_detection() | Time without detection after which all tracking servos are driven back to their neutral angle. |
|  |  |  |  |
| 4. Wheel follow behaviour (base rotation from eye deviation) |  |  |  |
| FACE_TRACKING_WHEEL_DEADZONE_DEG | "5.0" | FaceTrackingConfig.wheel_deadzone_deg, _build_face_tracking_config, _update_wheel_follow() | Minimum eye deviation (in degrees) from neutral before wheels start to turn. |
| FACE_TRACKING_WHEEL_FOLLOW_DELAY_S | "0.8" | FaceTrackingConfig.wheel_follow_delay_s, _build_face_tracking_config, _update_wheel_follow() | Delay between eye deviation crossing the threshold and wheels starting to follow. |
| FACE_TRACKING_WHEEL_INPUT_MIN_DEG | "30.0" | FaceTrackingConfig.wheel_input_min_deg, _build_face_tracking_config, _map_eye_to_wheel_target() | Lower bound of eye angle used for wheel mapping. |
| FACE_TRACKING_WHEEL_INPUT_MAX_DEG | "150.0" | FaceTrackingConfig.wheel_input_max_deg, _build_face_tracking_config | Upper bound of eye angle used for wheel mapping. |
| FACE_TRACKING_WHEEL_OUTPUT_MIN_DEG | "80.0" | FaceTrackingConfig.wheel_output_min_deg, _build_face_tracking_config | Minimum wheel target angle in mapping. |
| FACE_TRACKING_WHEEL_OUTPUT_MAX_DEG | "100.0" | FaceTrackingConfig.wheel_output_max_deg, _build_face_tracking_config | Maximum wheel target angle in mapping. |
| FACE_TRACKING_WHEEL_POWER | "2.0" | FaceTrackingConfig.wheel_power, _build_face_tracking_config, _remap_curved() | Exponent controlling non-linear mapping from eye deviation to wheel angle. |
|  |  |  |  |
| 5. Servo tuning overrides for tracking servos |  |  |  |
| prefix_map = { |  |  |  |
|     "EYL": "FACE_TRACKING_EYE", |  |  |  |
|     "EYR": "FACE_TRACKING_EYE", |  |  |  |
|     "NPT": "FACE_TRACKING_PITCH", |  |  |  |
|     "NRL": "FACE_TRACKING_YAW", |  |  |  |
|     "LWH": "FACE_TRACKING_WHEEL_LEFT", |  |  |  |
|     "RWH": "FACE_TRACKING_WHEEL_RIGHT", |  |  |  |
| } |  |  |  |
| For each prefix P in the table below, the following env vars are optionally read: |  |  |  |
| P_MIN_ANGLE_DEG |  | coglet-pi.py | If a specific env var is missing or invalid, the corresponding value from the base layout (SERVO_LAYOUT_V1) is used instead |
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
| FACE_TRACKING_EYE_MIN_ANGLE_DEG | not exported | servo_presets.py | not exported (base from SERVO_LAYOUT_V1["EYL"/"EYR"].config.min_angle_deg). |
| FACE_TRACKING_EYE_MAX_ANGLE_DEG | "150.0" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_EYE_MIN_PULSE_US | "600.0" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_EYE_MAX_PULSE_US | "2400.0" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_EYE_MAX_SPEED_DEG_PER_S | "200.0" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_EYE_MAX_ACCEL_DEG_PER_S2 | "1000.0" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_EYE_DEADZONE_DEG | "0.8" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_EYE_NEUTRAL_DEG | "90" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_EYE_INVERT | "0" | env-exports.sh | Eye servo overrides |
| FACE_TRACKING_PITCH_MIN_ANGLE_DEG | "1.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_MAX_ANGLE_DEG | "120.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_MIN_PULSE_US | "600.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_MAX_PULSE_US | "2400.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_MAX_SPEED_DEG_PER_S | "600.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_MAX_ACCEL_DEG_PER_S2 | "400.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_DEADZONE_DEG | "1.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_NEUTRAL_DEG | "50.0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_PITCH_INVERT | "0" | env-exports.sh | Pitch servo overrides |
| FACE_TRACKING_WHEEL_LEFT_MIN_ANGLE_DEG | "30." | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_ANGLE_DEG | "120.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_MIN_PULSE_US | "600.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_PULSE_US | "2400.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_SPEED_DEG_PER_S | "100.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_MAX_ACCEL_DEG_PER_S2 | "25.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_DEADZONE_DEG | "1.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_NEUTRAL_DEG | "90.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_LEFT_INVERT | "0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_MIN_ANGLE_DEG | "30.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_ANGLE_DEG | "120..0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_MIN_PULSE_US | "600.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_PULSE_US | "2400.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_SPEED_DEG_PER_S | "100.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_MAX_ACCEL_DEG_PER_S2 | "25.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_DEADZONE_DEG | "1.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_NEUTRAL_DEG | "90.0" | env-exports.sh | Wheel servo overrides |
| FACE_TRACKING_WHEEL_RIGHT_INVERT | "0" | env-exports.sh | Wheel servo overrides |
|  |  |  |  |
| 6. Eyelid / blinking controller |  |  |  |
| EYELID_OPEN_DEG | layout_cfg.neutral_deg (LID’s neutral angle) | coglet-pi.py | Absolute angle for “eyes open”. Clamped between servo min/max. |
| EYELID_CLOSED_DEG | open_angle - 60.0 (clamped between servo min/max) | coglet-pi.py | Absolute angle for “eyes closed”. |
| EYELID_SLEEP_FRACTION | 0.7 (clamped to 0.0–1.0) | coglet-pi.py | Fraction between open/closed used for “sleepy” half-closed state. |
| EYELID_BLINK_MIN_S | 3.0 | coglet-pi.py | Minimum random interval between blinks. |
| EYELID_BLINK_MAX_S | 7.0 | coglet-pi.py | Maximum random interval between blinks. |
| EYELID_BLINK_CLOSE_S | 0.06 | coglet-pi.py | Duration for the closing phase of a blink. |
| EYELID_BLINK_HOLD_S | 0.04 | coglet-pi.py | Time to hold eyes closed. |
| EYELID_BLINK_OPEN_S | 0.07 | coglet-pi.py | Duration for the opening phase of a blink. |
|  |  |  |  |
| 7. Generic animation servo overrides |  |  |  |
| For non-tracking servos (e.g. mouth, ears, head roll, etc.), _create_servo_setup() uses prefix = f"ANIM_{name}" for any servo name not explicitly listed in prefix_map. |  |  |  |
| This means you can control their mechanical limits and dynamics with the same pattern as above, but using the ANIM_ prefix, e.g.: |  |  |  |
| ANIM_MOU_MIN_ANGLE_DEG |  |  |  |
| ANIM_MOU_MAX_ANGLE_DEG |  |  |  |
| ANIM_MOU_MAX_SPEED_DEG_PER_S |  |  |  |
| ANIM_EAR_NEUTRAL_DEG |  |  |  |
| ANIM_EAR_INVERT |  |  |  |
| All these names are resolved purely via _create_servo_config() and fall back to the corresponding SERVO_LAYOUT_V1 defaults in servo_presets.py if not set. |  |  |  |
