"""Apply map edits (drag/presets) or a text instruction (Hydrogen) to a scene.

Spec: S09 (.claude/specs/S09*.md) — owner: Akash.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

def apply_edit(scene, edit: dict):
    raise NotImplementedError


def text_edit(scene, instruction: str):
    raise NotImplementedError
