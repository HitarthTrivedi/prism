"""
Prism Motion Graphics Audio Subsystem
─────────────────────────────────────
Multiplexes voiceover narration with background music and automatic sidechain ducking.
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional


class AudioError(Exception):
    pass


def mux_audio_and_video(
    video_path: str,
    voiceover_path: Optional[str],
    output_path: str,
    bgm_path: Optional[str] = None,
    bgm_volume: float = 0.25,
) -> str:
    """Mux voiceover and background music with video, applying sidechain ducking."""
    if not voiceover_path and not bgm_path:
        return video_path

    from core import ffmpeg
    ff = ffmpeg.locate()
    if not ff or not os.path.exists(ff):
        raise AudioError("FFmpeg executable not found for audio muxing.")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if voiceover_path and bgm_path:
        filter_complex = (
            f"[1:a]asplit=2[sc][voice];"
            f"[2:a]volume={bgm_volume}[bgm_norm];"
            f"[bgm_norm][sc]sidechaincompress=threshold=0.12:ratio=4:attack=40:release=350[bgm_ducked];"
            f"[voice][bgm_ducked]amix=inputs=2:duration=first[aout]"
        )
        cmd = [
            ff, "-y",
            "-i", video_path,
            "-i", voiceover_path,
            "-i", bgm_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]
    elif voiceover_path:
        cmd = [
            ff, "-y",
            "-i", video_path,
            "-i", voiceover_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]
    else:
        cmd = [
            ff, "-y",
            "-i", video_path,
            "-i", bgm_path,
            "-filter:a", f"volume={bgm_volume}",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise AudioError(f"Audio muxing failed: {res.stderr}")

    return output_path
