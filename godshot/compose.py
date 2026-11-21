"""ffmpeg: clip over map → 1080×1920 reel; compare layout for re-shoots.

Spec: S08 (.claude/specs/S08*.md) — owner: Akash.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def compose_reel(clip: Path, map_mp4: Path, out: Path, title: str | None = None, credit: str | None = None) -> Path:
    raise NotImplementedError


def compose_compare(clip_a: Path, clip_b: Path, map_mp4: Path, out: Path) -> Path:
    raise NotImplementedError
