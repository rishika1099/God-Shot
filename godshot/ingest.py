"""Upload clip once (Files API) and extract exact-timestamp frames with ffmpeg.

Spec: S01 (.claude/specs/S01*.md) — owner: Prajesh.

Frame files are named by milliseconds: `out_dir/{round(t*1000):06d}.jpg` (t=2.0 → 002000.jpg), which
is what check.v1 `input_ref.paths` point at (`frames/<clip_id>/002000.jpg`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from godshot.cache import CacheMiss, is_offline
from godshot.config import get_client, load_config
from godshot.schema import Clip

log = logging.getLogger(__name__)

MAX_CLIP_S = 35.0
WARN_CLIP_S = 15.0
UPLOAD_TIMEOUT_S = 120.0
UPLOAD_POLL_S = 2.0
UPLOAD_MAX_AGE = timedelta(hours=40)  # Files API URIs expire (~48 h); re-upload before that
FRAME_WORKERS = 4


class IngestError(RuntimeError):
    """ffmpeg/ffprobe failure, bad clip, or failed upload."""


class ClipTooLongError(IngestError):
    """Clip is longer than MAX_CLIP_S."""


# --------------------------------------------------------------------------- helpers


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise IngestError(f"{name} not found on PATH. Install ffmpeg (macOS: `brew install ffmpeg`).")
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-5:]
        raise IngestError(f"{Path(cmd[0]).name} failed ({proc.returncode}): {' | '.join(tail)}")
    return proc


def frame_name(t: float) -> str:
    """Frame filename for timestamp t (seconds): milliseconds, zero-padded to 6 digits."""
    if t < 0:
        raise ValueError(f"timestamp must be >= 0, got {t}")
    return f"{round(t * 1000):06d}.jpg"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _parse_rate(rate: str | None) -> float:
    if not rate or rate in ("0/0", "0"):
        return 0.0
    return float(Fraction(rate))


# --------------------------------------------------------------------------- probe


def probe(clip: Path, check_length: bool = True) -> Clip:
    """ffprobe → Clip(id=stem, duration_s, fps, width, height).

    With `check_length`, clips > 35 s raise ClipTooLongError and clips > 15 s log a warning.
    """
    clip = Path(clip)
    if not clip.is_file():
        raise IngestError(f"clip not found: {clip}")
    proc = _run(
        [
            _tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,duration:format=duration",
            "-of", "json", str(clip),
        ]
    )  # fmt: skip
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise IngestError(f"no video stream in {clip}")
    st = streams[0]
    fps = _parse_rate(st.get("avg_frame_rate")) or _parse_rate(st.get("r_frame_rate"))
    # Prefer the VIDEO stream duration: the container is often longer when audio runs past the last frame
    # (Omni outputs: video 5.000 s, container 5.035 s), and frames past the video end don't exist.
    duration = float(st.get("duration") or info.get("format", {}).get("duration") or 0)
    if fps <= 0 or duration <= 0:
        raise IngestError(f"could not read fps/duration from {clip} (fps={fps}, duration={duration})")

    if check_length and duration > MAX_CLIP_S:
        raise ClipTooLongError(
            f"{clip.name} is {duration:.1f} s; the limit is {MAX_CLIP_S:.0f} s. Trim it first (ingest.trim)."
        )
    if check_length and duration > WARN_CLIP_S:
        log.warning(
            "%s is %.1f s (> %.0f s): expect slower, less accurate runs", clip.name, duration, WARN_CLIP_S
        )

    return Clip(
        id=clip.stem,
        duration_s=round(duration, 3),
        fps=round(fps, 3),
        width=int(st["width"]),
        height=int(st["height"]),
    )


# --------------------------------------------------------------------------- frames


def frame_at(clip: Path, t: float, out_dir: Path, max_w: int = 768) -> Path:
    """Extract the frame at `t` seconds as JPEG, scaled to `max_w` wide. Idempotent (skips existing)."""
    out_dir = Path(out_dir)
    out = out_dir / frame_name(t)
    if out.exists():
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".tmp-{out.name}"  # keep .jpg suffix so ffmpeg picks the image2 muxer
    try:
        _run(
            [
                _tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{t:.3f}", "-i", str(clip),
                "-frames:v", "1", "-vf", f"scale={max_w}:-2", "-q:v", "2", str(tmp),
            ]
        )  # fmt: skip
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise IngestError(f"no frame at t={t:.3f}s in {Path(clip).name} (past the end of the clip?)")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def frames_at(clip: Path, ts: list[float], out_dir: Path, max_w: int = 768) -> list[Path]:
    """Extract many frames in parallel (4 workers). Returns paths in the order of `ts`."""
    unique = list(dict.fromkeys(frame_name(t) for t in ts))
    by_name = {frame_name(t): t for t in ts}
    with ThreadPoolExecutor(max_workers=FRAME_WORKERS) as pool:
        paths = dict(
            zip(unique, pool.map(lambda n: frame_at(clip, by_name[n], out_dir, max_w), unique), strict=True)
        )
    return [paths[frame_name(t)] for t in ts]


def trim(clip: Path, t0: float, t1: float, out: Path) -> Path:
    """Re-encode [t0, t1) of `clip` to `out` (H.264, frame-accurate). For stress tests and Omni refs."""
    if t0 < 0 or t1 <= t0:
        raise ValueError(f"need 0 <= t0 < t1, got t0={t0}, t1={t1}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            _tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{t0:.3f}", "-i", str(clip), "-t", f"{t1 - t0:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-movflags", "+faststart", str(out),
        ]
    )  # fmt: skip
    return out


# --------------------------------------------------------------------------- upload


def _upload_record_path(digest: str) -> Path:
    return Path(load_config().CACHE_DIR) / "uploads" / f"{digest}.json"


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _aware(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # treat naive timestamps as UTC


def _is_fresh(rec: dict[str, Any], now: datetime) -> bool:
    if now - _aware(rec["uploaded_at"]) >= UPLOAD_MAX_AGE:
        return False
    exp = rec.get("expiration_time")
    return not (exp and _aware(exp) - now < timedelta(hours=1))


def _wait_active(client: Any, file: Any, timeout_s: float, poll_s: float) -> Any:
    deadline = time.monotonic() + timeout_s
    while True:
        state = getattr(getattr(file, "state", None), "name", str(getattr(file, "state", "")))
        if state == "ACTIVE":
            return file
        if state == "FAILED":
            raise IngestError(f"Files API processing FAILED for {file.name}: {getattr(file, 'error', None)}")
        if time.monotonic() > deadline:
            raise IngestError(f"upload {file.name} not ACTIVE after {timeout_s:.0f} s (state={state})")
        time.sleep(poll_s)
        file = client.files.get(name=file.name)


def upload(clip: Path, timeout_s: float = UPLOAD_TIMEOUT_S, poll_s: float = UPLOAD_POLL_S) -> str:
    """Upload once via the Files API and return the file URI. Cached by clip sha256 for < 40 h.

    GODSHOT_OFFLINE=1: returns the cached URI even if stale (downstream calls are served from cache);
    raises CacheMiss if the clip was never uploaded.
    """
    clip = Path(clip)
    digest = sha256_file(clip)
    rec_path = _upload_record_path(digest)
    rec = _read_record(rec_path)
    now = datetime.now(UTC)

    if rec and (is_offline() or _is_fresh(rec, now)):
        log.debug("upload cache hit %s → %s", clip.name, rec["uri"])
        return rec["uri"]
    if is_offline():
        raise CacheMiss(f"GODSHOT_OFFLINE=1 and {clip.name} was never uploaded")

    client = get_client()
    t0 = time.perf_counter()
    file = client.files.upload(file=str(clip))
    file = _wait_active(client, file, timeout_s, poll_s)
    exp = getattr(file, "expiration_time", None)
    record = {
        "uri": file.uri,
        "name": file.name,
        "mime_type": getattr(file, "mime_type", None),
        "clip": clip.name,
        "sha256": digest,
        "uploaded_at": now.isoformat(),
        "expiration_time": exp.isoformat() if isinstance(exp, datetime) else exp,
    }
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    log.info("uploaded %s in %.1f s → %s", clip.name, time.perf_counter() - t0, file.uri)
    return file.uri
