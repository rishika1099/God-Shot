"""Decisions API (gemini-decision-1): answer checks against frames, set verdicts.

Spec: S05 (.claude/specs/S05*.md) — owner: Siva.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def verify(checks: list, frames_dir: Path) -> list:
    raise NotImplementedError


def ask(question, images: list[Path], context: str):
    raise NotImplementedError


def summary(checks: list) -> dict:
    raise NotImplementedError
