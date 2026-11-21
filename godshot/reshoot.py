"""Omni re-shoot from an EDITED scene + conformance check against that edit.

Spec: S12 (.claude/specs/S12*.md) — owner: Prajesh.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def describe_shot(scene_edited) -> str:
    raise NotImplementedError


def reshoot(clip: Path, scene_orig, scene_edited, out: Path, resolution: str = "360p", duration: str = "8s") -> Path:
    raise NotImplementedError


def conformance(generated_clip: Path, scene_edited) -> dict:
    raise NotImplementedError
