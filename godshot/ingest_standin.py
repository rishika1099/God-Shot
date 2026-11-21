"""TEMPORARY stand-in for S01 `probe` / `upload` until Prajesh's `godshot/ingest.py` lands.

Used by S02 (`extract.py`) only when `godshot.ingest` still raises NotImplementedError.
Delete this file once S01 is merged; nothing else should import it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from godshot.schema import Clip

log = logging.getLogger(__name__)

# Dummy metadata when ffprobe is not installed. Good enough to exercise the pipeline, not for eval.
DEFAULT_DURATION_S = 10.0
DEFAULT_FPS = 24.0
DEFAULT_W, DEFAULT_H = 1280, 720
UPLOAD_TIMEOUT_S = 120


def _parse_rate(rate: str) -> float:
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1) if float(den or 1) else 0.0


def probe(clip: Path) -> Clip:
    """ffprobe if available, else dummy metadata (logged as a warning)."""
    clip = Path(clip)
    if not shutil.which("ffprobe"):
        log.warning("ffprobe not found: using dummy clip metadata for %s (S01 stand-in)", clip.name)
        return Clip(id=clip.stem, duration_s=DEFAULT_DURATION_S, fps=DEFAULT_FPS, width=DEFAULT_W,
                    height=DEFAULT_H)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate:format=duration", "-of", "json", str(clip)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return Clip(
        id=clip.stem,
        duration_s=float(data["format"]["duration"]),
        fps=_parse_rate(stream.get("avg_frame_rate", "0/1")) or DEFAULT_FPS,
        width=int(stream["width"]),
        height=int(stream["height"]),
    )


def upload(clip: Path) -> str:
    """Files API upload, poll until ACTIVE. No URI caching (S01 adds that)."""
    from godshot.config import get_client

    client = get_client()
    f = client.files.upload(file=str(clip))
    deadline = time.monotonic() + UPLOAD_TIMEOUT_S
    while f.state is not None and f.state.name == "PROCESSING":
        if time.monotonic() > deadline:
            raise TimeoutError(f"upload of {clip} not ACTIVE after {UPLOAD_TIMEOUT_S}s")
        time.sleep(2)
        f = client.files.get(name=f.name or "")
    if f.state is not None and f.state.name != "ACTIVE":
        raise RuntimeError(f"upload of {clip} ended in state {f.state.name}")
    return f.uri or ""
