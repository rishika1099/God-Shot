"""Disk cache for every API response. GODSHOT_OFFLINE=1 → raise on miss.

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def cached(key_parts: list, fn: Callable[[], T], kind: str) -> T:
    raise NotImplementedError
