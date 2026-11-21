"""Disk cache for every API response. GODSHOT_OFFLINE=1 → raise on miss.

Spec: S00 (.claude/specs/S00*.md) — owner: Prajesh.

Usage:
    resp = cached([clip_bytes, cfg.MODEL_EXTRACT, prompt, json.dumps(params, sort_keys=True)],
                  lambda: call_api(...), kind="extract")

`fn` must return JSON-serialisable data (dict/list/str/number) or `bytes`. Convert SDK response
objects to dicts (e.g. `resp.model_dump(mode="json")`) inside `fn`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from godshot.config import load_config

T = TypeVar("T")
log = logging.getLogger(__name__)

_KIND_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class CacheMiss(RuntimeError):
    """Raised in offline mode (GODSHOT_OFFLINE=1) when a response is not cached."""


def is_offline() -> bool:
    return os.environ.get("GODSHOT_OFFLINE", "").strip() not in ("", "0", "false", "False")


def cache_key(key_parts: Sequence[bytes | str]) -> str:
    """sha256 over the parts, length-prefixed so ['ab','c'] and ['a','bc'] differ."""
    h = hashlib.sha256()
    for part in key_parts:
        b = part if isinstance(part, bytes) else str(part).encode("utf-8")
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return h.hexdigest()


def _cache_dir() -> Path:
    return Path(load_config().CACHE_DIR)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def cached(key_parts: Sequence[bytes | str], fn: Callable[[], T], kind: str) -> T:
    """Return the cached result for `key_parts`, or call `fn`, store and return it.

    Stored at CACHE_DIR/<kind>/<sha256>.json (JSON results) or .bin (bytes results).
    """
    if not _KIND_RE.match(kind):
        raise ValueError(f"invalid cache kind {kind!r}")
    key = cache_key(key_parts)
    base = _cache_dir() / kind
    json_path, bin_path = base / f"{key}.json", base / f"{key}.bin"

    if json_path.exists():
        log.debug("cache hit %s/%s", kind, key[:12])
        return json.loads(json_path.read_text(encoding="utf-8"))
    if bin_path.exists():
        log.debug("cache hit %s/%s (bytes)", kind, key[:12])
        return bin_path.read_bytes()  # type: ignore[return-value]

    if is_offline():
        raise CacheMiss(f"GODSHOT_OFFLINE=1 and no cache entry for {kind}/{key[:12]}")

    log.debug("cache miss %s/%s → calling API", kind, key[:12])
    result = fn()
    if isinstance(result, bytes | bytearray):
        _atomic_write(bin_path, bytes(result))
        return result
    text = json.dumps(result, ensure_ascii=False, indent=1)
    _atomic_write(json_path, text.encode("utf-8"))
    return json.loads(text)  # same shape on hit and miss (tuples → lists, etc.)
