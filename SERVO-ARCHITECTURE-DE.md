```
                        ┌──────────────────────────────────-────┐
                        │          Coglet-Hauptprogramm         │
                        │ (main() / demomode() / Signalhandler) │
                        └───────────────────────────────────-───┘
                                          │
                                          │
                           ruft auf ↓ _initialize_all_servos()
                                          │
                                          ▼
         ┌────────────────────────────────────────────────────────┐
         │               _initialize_all_servos(logger)           │
         │────────────────────────────────────────────────────────│
         │ • load_servo_calibration(logger)                       │
         │ • baut servo_channel_map für 10 Servos                 │
         │ • wendet apply_calibration_to_config() an              │
         │ • erzeugt Servo()-Objekte (→ start_deg / neutral)      │
         │ • loggt Kalibrierung vs. Preset-Nutzung                │
         │ • registriert alle Servos für Shutdown                 │
         │ • _create_eyelid_controller() + _set_eyelids()         │
         │ • _register_anim_servos()  [NRL, MOU, EAL, EAR]        │
         │ • gibt {servo_map, calibration_map, channel_map, pca}  │
         │   zurück                                               │
         └────────────────────────────────────────────────────────┘
                                          │
                                          │
                     übergibt Strukturen an ↓
                                          │
                                          ▼
         ┌────────────────────────────────────────────────────────┐
         │                   _setup_face_tracking()               │
         │────────────────────────────────────────────────────────│
         │ • nutzt vorhandene servo_map aus Init                  │
         │ • konstruiert FaceTrackingServos                       │
         │     (EYL, EYR, NPT, LWH, RWH, optional NRL)            │
         │ • verbindet sich mit Grove Vision AI v2                │
         │ • startet Tracking-Loop-Thread                         │
         │ • gibt FaceTracker-Instanz zurück                      │
         └────────────────────────────────────────────────────────┘
                                          │
                                          ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                           Servo-Steuerung zur Laufzeit                       │
 │──────────────────────────────────────────────────────────────────────────────│
 │                                                                              │
 │  ┌──────────────┐   ┌───────────────────┐   ┌────────────────────────────┐   │
 │  │ FaceTracker  │   │ EyelidController  │   │ Personality Animations     │   │
 │  │──────────────│   │───────────────────│   │────────────────────────────│   │
 │  │ Steuert:     │   │ Steuert: LID      │   │ Steuert: NRL, MOU, EAL,    │   │
 │  │  EYL,EYR     │   │ Modus: "auto"     │   │          EAR               │   │
 │  │  NPT,NRL?    │   │   (Blink-Thread)  │   │                            │   │
 │  │  LWH,RWH     │   │ Neue API:         │   │ anim_listen_start()        │   │
 │  │              │   │  set_override(    │   │ anim_listen_stop()         │   │
 │  │              │   │     angle, dur)   │   │ anim_think_start()         │   │
 │  │              │   │ → Blink aussetzen │   │ anim_think_stop()          │   │
 │  └──────────────┘   └───────────────────┘   │                            │   │
 │                                             │ • _curious_loop()          │   │
 │                                             │ • _thinking_loop()         │   │
 │                                             │ (threadbasierte Loops)     │   │
 │                                             └────────────────────────────┘   │
 │                                                                              │
 │  Koordination:                                                               │
 │   - jede Animation läuft in eigenem Thread mit StopEvent                     │
 │   - FaceTracking läuft kontinuierlich                                        │
 │   - EyelidController übernimmt Blinzeln außer während Override               │
 │                                                                              │
 └──────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
         ┌────────────────────────────────────────────────────────┐
         │             Programmende / Shutdown                    │
         │────────────────────────────────────────────────────────│
         │ _restore_neutral_pose_and_close_lid():                 │
         │   → _move_servos_to_stop_positions()                   │
         │       nutzt calibration.clamped_stop pro Kanal         │
         │   → _eyelids_set_mode("closed")                        │
         │                                                        │
         │ Registriert aus _initialize_all_servos()               │
         │ ausgelöst durch:                                       │
         │   - graceful SIGINT/SIGTERM                            │
         │   - finally:-Blöcke in main() und demomode()           │
         └────────────────────────────────────────────────────────┘


Vereinfachte Servo-Runtime-Dynamik:
main()
 ├─ _initialize_all_servos()
 │    ├─ lädt Kalibrierung
 │    ├─ erstellt servo_map
 │    ├─ startet EyelidController
 │    ├─ registriert anim_servos + shutdown_targets
 │    └─ setzt Startwinkel
 │
 ├─ _setup_face_tracking()
 │    └─ startet FaceTracking-Thread
 │
 ├─ Voice-Interaktionsloop
 │    ├─ anim_listen_start()   → _curious_loop() + Eyelid-Override
 │    ├─ anim_think_start()    → _thinking_loop()
 │    ├─ anim_talk_start()     → _mouth_loop()
 │    └─ jeweiliges *_stop()   → Threads stoppen + Neutralpose wiederherstellen
 │
 └─ geordneter Shutdown
      └─ _move_servos_to_stop_positions()
           → clamped_stop pro Kalibrierung
           → Lider geschlossen



```
