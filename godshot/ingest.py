"""Upload clip once (Files API) and extract exact-timestamp frames with ffmpeg.

Spec: S01 (.claude/specs/S01*.md) — owner: Prajesh.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def probe(clip: Path):
    raise NotImplementedError


def upload(clip: Path) -> str:
    raise NotImplementedError


def frame_at(clip: Path, t: float, out_dir: Path, max_w: int = 768) -> Path:
    raise NotImplementedError


def frames_at(clip: Path, ts: list[float], out_dir: Path) -> list[Path]:
    raise NotImplementedError


def trim(clip: Path, t0: float, t1: float, out: Path) -> Path:
    raise NotImplementedError
