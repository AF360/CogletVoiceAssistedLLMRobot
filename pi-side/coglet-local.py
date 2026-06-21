#!/usr/bin/env python3
"""Launch Coglet's local wakeword/STT/LLM/TTS mode."""
from __future__ import annotations

import os

import local_mode
from robot_runtime import get_anim_servo

local_mode._get_anim_servo = get_anim_servo
local_mode.__version__ = os.getenv("COGLET_VERSION", "1.1.1")

if __name__ == "__main__":
    raise SystemExit(local_mode.main())
