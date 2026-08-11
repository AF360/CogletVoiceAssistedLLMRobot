#!/usr/bin/env python3
"""Launch Coglet's local speech pipeline without robot hardware."""
from __future__ import annotations

import os
import sys

import voice_runtime


# local_mode imports a module named ``robot_runtime``.  Substitute the small
# voice-only facade before importing local_mode, so PCA9685, servo, camera and
# XVF3800 control modules are never imported by this launcher.
sys.modules["robot_runtime"] = voice_runtime

import local_mode  # noqa: E402  (must follow the runtime substitution)


local_mode._get_anim_servo = voice_runtime.get_anim_servo
local_mode.__version__ = os.getenv("COGLET_VERSION", "1.1.2") + "-voice"


if __name__ == "__main__":
    raise SystemExit(local_mode.main())
