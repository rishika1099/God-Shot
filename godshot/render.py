"""scene.v1 → map.mp4 (overhead animation, frame-synced to the clip).

Spec: S07 (.claude/specs/S07*.md) — owner: Akash.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def render_map(scene, out: Path, fps: float | None = None, size=(1080, 960), ghost=None) -> Path:
    raise NotImplementedError
