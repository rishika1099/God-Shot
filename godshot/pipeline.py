"""Loop controller: extract → (checks → verify → correct)×≤3, keep-if-better. Used by CLI + web.

Spec: S06 (.claude/specs/S06*.md) — owner: Prajesh.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from pathlib import Path


def run(clip: Path, out_dir: Path):
    raise NotImplementedError
