"""
Prism Motion Graphics Engine (`core.motion`)
─────────────────────────────────────────────
AI-driven, deterministic, programmatic motion graphics engine for Prism.

Pipeline Architecture:
  User Prompt
    → AI Motion Planner (semantic Motion JSON)
    → Prism Motion Runtime (Scene Graph, Camera, RK4 Springs, Timeline)
    → Deterministic Frame Renderer (Canvas2D / WebGL)
    → FFmpeg (`core.ffmpeg`)
    → 1080x1920 MP4
"""
from __future__ import annotations

from .schema import MotionProject, validate_motion_spec, MotionValidationError
from .resolver import resolve_motion_spec
from .render import render, is_available, MotionRenderError
from .audio import mux_audio_and_video, AudioError

__all__ = [
    "MotionProject",
    "validate_motion_spec",
    "MotionValidationError",
    "resolve_motion_spec",
    "render",
    "is_available",
    "MotionRenderError",
    "mux_audio_and_video",
    "AudioError",
]
