"""Argon: clip → scene.v1. Round 0 of the loop == the eval BASELINE.

Spec: S02 (.claude/specs/S02*.md) — owner: Akash.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def extract(clip: Path, fps: int | None = None):
    raise NotImplementedError


def postprocess(scene):
    """Clamp coords, sort keys, add mmss, fix yaw jumps, dedupe descriptions."""
    raise NotImplementedError
