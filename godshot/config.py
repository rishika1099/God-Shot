"""Load .env and expose model IDs + pipeline settings. The ONLY place model IDs live.

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from google import genai

REPO_ROOT = Path(__file__).resolve().parent.parent

# Defaults mirror .env.example. VERIFY: model IDs confirmed via `python -m godshot smoke`.
_DEFAULTS: dict[str, str] = {
    "GODSHOT_MODEL_EXTRACT": "gemini-4-argon",
    "GODSHOT_MODEL_CORRECT": "gemini-4-argon",
    "GODSHOT_MODEL_CHEAP": "gemini-4-hydrogen",
    "GODSHOT_MODEL_VERIFY": "gemini-decision-1",
    "GODSHOT_MODEL_RESHOOT": "gemini-omni-1.2-flash",
    "GODSHOT_MODEL_IMAGE": "",
    # Re-shoot video backend: omni (Gemini Omni, default) | wan_vace (Wan VACE 14B on fal.ai, needs FAL_KEY).
    "GODSHOT_RESHOOT_BACKEND": "omni",
    "GODSHOT_MODEL_WAN": "fal-ai/wan-vace-14b",
    "GODSHOT_WAN_TASK": "depth",
    "GODSHOT_FPS": "4",
    "GODSHOT_MAX_ROUNDS": "3",
    "GODSHOT_PASS_THRESHOLD": "0.7",
    "GODSHOT_FAIL_THRESHOLD": "0.3",
    "GODSHOT_CACHE_DIR": ".cache",
}


class ConfigError(RuntimeError):
    """Raised when required configuration (e.g. the API key) is missing."""


@dataclass(frozen=True)
class Config:
    API_KEY: str
    MODEL_EXTRACT: str
    MODEL_CORRECT: str
    MODEL_CHEAP: str
    MODEL_VERIFY: str
    MODEL_RESHOOT: str
    MODEL_IMAGE: str
    RESHOOT_BACKEND: str
    MODEL_WAN: str
    WAN_TASK: str
    FPS: int
    MAX_ROUNDS: int
    PASS_THRESHOLD: float
    FAIL_THRESHOLD: float
    CACHE_DIR: str
    FAL_KEY: str = ""

    def __repr__(self) -> str:  # never leak the key into logs
        fields = {k: v for k, v in self.__dict__.items() if k not in ("API_KEY", "FAL_KEY")}
        api, fal = ("set" if k else "missing" for k in (self.API_KEY, self.FAL_KEY))
        return f"Config(API_KEY=<{api}>, FAL_KEY=<{fal}>, {fields})"


def _env(name: str) -> str:
    return os.environ.get(name, _DEFAULTS.get(name, "")).strip()


def _resolve_cache_dir(raw: str) -> str:
    p = Path(raw)
    return str(p if p.is_absolute() else REPO_ROOT / p)


@lru_cache(maxsize=2)
def load_config(require_key: bool = False) -> Config:
    """Read `.env` (repo root) + process env. Set `require_key=True` before any API call."""
    load_dotenv(REPO_ROOT / ".env", override=False)
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if require_key and not api_key:
        raise ConfigError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and fill in the key (never commit .env)."
        )
    return Config(
        API_KEY=api_key,
        MODEL_EXTRACT=_env("GODSHOT_MODEL_EXTRACT"),
        MODEL_CORRECT=_env("GODSHOT_MODEL_CORRECT"),
        MODEL_CHEAP=_env("GODSHOT_MODEL_CHEAP"),
        MODEL_VERIFY=_env("GODSHOT_MODEL_VERIFY"),
        MODEL_RESHOOT=_env("GODSHOT_MODEL_RESHOOT"),
        MODEL_IMAGE=_env("GODSHOT_MODEL_IMAGE"),
        RESHOOT_BACKEND=_env("GODSHOT_RESHOOT_BACKEND").lower(),
        MODEL_WAN=_env("GODSHOT_MODEL_WAN"),
        WAN_TASK=_env("GODSHOT_WAN_TASK"),
        FPS=int(_env("GODSHOT_FPS")),
        MAX_ROUNDS=int(_env("GODSHOT_MAX_ROUNDS")),
        PASS_THRESHOLD=float(_env("GODSHOT_PASS_THRESHOLD")),
        FAIL_THRESHOLD=float(_env("GODSHOT_FAIL_THRESHOLD")),
        CACHE_DIR=_resolve_cache_dir(_env("GODSHOT_CACHE_DIR")),
        FAL_KEY=os.environ.get("FAL_KEY", "").strip(),
    )


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    """Shared genai.Client on v1beta (needed for the Decisions API). Raises ConfigError without a key.

    One client per process: a client that is garbage-collected closes its HTTP connection, which broke
    lazy pagers like `get_client().models.list()`. The client is thread-safe (S05 uses a thread pool).
    Tests that change the key should call `get_client.cache_clear()`.
    """
    from google import genai

    cfg = load_config(require_key=True)
    return genai.Client(api_key=cfg.API_KEY, http_options={"api_version": "v1beta"})
