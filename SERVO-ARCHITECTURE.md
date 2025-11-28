```
                        ┌──────────────────────────────────-────┐
                        │          Coglet Main Program          │
                        │ (main() / demomode() / signal handler)│
                        └───────────────────────────────────-───┘
                                          │
                                          │
                           calls ↓ _initialize_all_servos()
                                          │
                                          ▼
         ┌────────────────────────────────────────────────────────┐
         │               _initialize_all_servos(logger)           │
         │────────────────────────────────────────────────────────│
         │ • load_servo_calibration(logger)                       │
         │ • build servo_channel_map for 10 servos                │
         │ • apply apply_calibration_to_config()                  │
         │ • instantiate Servo() objects (→ start_deg / neutral)  │
         │ • log calibration vs. preset use                       │
         │ • register all servos for shutdown                     │
         │ • _create_eyelid_controller() + _set_eyelids()         │
         │ • _register_anim_servos()  [NRL, MOU, EAL, EAR]        │
         │ • return {servo_map, calibration_map, channel_map, pca}│
         └────────────────────────────────────────────────────────┘
                                          │
                                          │
                     passes structures to ↓
                                          │
                                          ▼
         ┌────────────────────────────────────────────────────────┐
         │                   _setup_face_tracking()               │
         │────────────────────────────────────────────────────────│
         │ • uses existing servo_map from init                    │
         │ • constructs FaceTrackingServos                        │
         │     (EYL, EYR, NPT, LWH, RWH, optional NRL)            │
         │ • connects to Grove Vision AI v2                       │
         │ • starts tracking loop thread                          │
         │ • returns FaceTracker instance                         │
         └────────────────────────────────────────────────────────┘
                                          │
                                          ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                           Runtime Servo Control                              │
 │──────────────────────────────────────────────────────────────────────────────│
 │                                                                              │
 │  ┌──────────────┐   ┌───────────────────┐   ┌────────────────────────────┐   │
 │  │ FaceTracker  │   │ EyelidController  │   │ Personality Animations     │   │
 │  │──────────────│   │───────────────────│   │────────────────────────────│   │
 │  │ Controls:    │   │ Controls: LID     │   │ Controls: NRL, MOU, EAL,   │   │
 │  │  EYL,EYR     │   │ Mode: "auto"      │   │             EAR            │   │
 │  │  NPT,NRL?    │   │   (blink thread)  │   │                            │   │
 │  │  LWH,RWH     │   │ New API:          │   │ anim_listen_start()        │   │
 │  │              │   │  set_override(    │   │ anim_listen_stop()         │   │
 │  │              │   │     angle, dur)   │   │ anim_think_start()         │   │
 │  │              │   │ → suspend blink   │   │ anim_think_stop()          │   │
 │  └──────────────┘   └───────────────────┘   │                            │   │
 │                                             │ • _curious_loop()          │   │
 │                                             │ • _thinking_loop()         │   │
 │                                             │ (thread-based loops)       │   │
 │                                             └────────────────────────────┘   │
 │                                                                              │
 │  Coordination:                                                               │
 │   - each animation runs in its own thread with StopEvent                     │
 │   - FaceTracking runs continuously                                           │
 │   - EyelidController handles blinking except during override                 │
 │                                                                              │
 └──────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
         ┌────────────────────────────────────────────────────────┐
         │             Program Termination / Shutdown             │
         │────────────────────────────────────────────────────────│
         │ _restore_neutral_pose_and_close_lid():                 │
         │   → _move_servos_to_stop_positions()                   │
         │       uses calibration.clamped_stop per channel        │
         │   → _eyelids_set_mode("closed")                        │
         │                                                        │
         │ Registered from _initialize_all_servos()               │
         │ triggered by:                                          │
         │   - graceful SIGINT/SIGTERM                            │
         │   - finally: blocks in main() and demomode()           │
         └────────────────────────────────────────────────────────┘


Simplified servo runtime dynamics:
main()
 ├─ _initialize_all_servos()
 │    ├─ loads calibration
 │    ├─ creates servo_map
 │    ├─ starts EyelidController
 │    ├─ registers anim_servos + shutdown_targets
 │    └─ sets start angles
 │
 ├─ _setup_face_tracking()
 │    └─ starts FaceTracking thread
 │
 ├─ Voice interaction loop
 │    ├─ anim_listen_start()   → _curious_loop() + eyelid override
 │    ├─ anim_think_start()    → _thinking_loop()
 │    ├─ anim_talk_start()     → _mouth_loop()
 │    └─ respective *_stop()   → stop threads + restore neutral pose
 │
 └─ graceful shutdown
      └─ _move_servos_to_stop_positions()
           → clamped_stop per calibration
           → lids closed



```
