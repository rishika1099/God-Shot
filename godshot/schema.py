"""Pydantic models for scene.v1, check.v1, label.v1. FROZEN after S00 (see .claude/docs/contracts.md).

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pydantic import BaseModel


def mmss(t: float) -> str:
    raise NotImplementedError


class SceneV1(BaseModel):
    """TODO(S00): full model per contracts.md."""


class CheckV1(BaseModel):
    """TODO(S00): full model per contracts.md."""


class LabelV1(BaseModel):
    """TODO(S00): full model per contracts.md."""
