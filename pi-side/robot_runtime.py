"""Public facade for Coglet's shared physical robot runtime."""
from __future__ import annotations

from hardware.robot_runtime import *
from hardware.robot_runtime import _XVF_MIC_AVAILABLE, _get_anim_servo

XVF_MIC_AVAILABLE = _XVF_MIC_AVAILABLE
get_anim_servo = _get_anim_servo


import builtins as _builtins
_builtins._get_anim_servo = _get_anim_servo

try:
    from hardware.robot_runtime import ReSpeakerMic
except ImportError:
    ReSpeakerMic = None
