"""scene.v1 → check.v1 list from geometry templates (code-generated, never LLM).

Spec: S04 (.claude/specs/S04*.md) — owner: Siva.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def make_checks(scene, frames_dir: Path, sample_ts: list[float] | None = None) -> list:
    raise NotImplementedError


def frames_needed(checks: list) -> list[float]:
    raise NotImplementedError
