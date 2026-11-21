"""Load .env and expose model IDs + pipeline settings. The ONLY place model IDs live.

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    API_KEY: str
    MODEL_EXTRACT: str
    MODEL_CORRECT: str
    MODEL_CHEAP: str
    MODEL_VERIFY: str
    MODEL_RESHOOT: str
    MODEL_IMAGE: str
    FPS: int
    MAX_ROUNDS: int
    PASS_THRESHOLD: float
    FAIL_THRESHOLD: float
    CACHE_DIR: str


def load_config() -> Config:
    raise NotImplementedError


def get_client():
    """genai.Client(http_options={"api_version": "v1beta"})."""
    raise NotImplementedError
