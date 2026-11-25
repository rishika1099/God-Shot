"""Optional re-shoot backend: Wan VACE 14B on fal.ai, driven by God-Shot's guide video.

Spec: S12 (.claude/specs/S12-reshoot.md), optional backend. Select with GODSHOT_RESHOOT_BACKEND=wan_vace
(or `reshoot --backend wan_vace`). Needs FAL_KEY in .env and `pip install -r requirements-wan.txt`.

Why it fits: VACE takes a control video. We send the map-exact guide (blockout or the 3D reprojection)
with `task="depth"` and `preprocess=True`, so fal turns the guide into a depth control and Wan paints
the real scene over that exact geometry. The output keeps the guide's frame count and fps, so the
re-shoot lines up with the edited map frame for frame.

API (verified 2026-11-25 against https://fal.ai/models/fal-ai/wan-vace-14b/llms.txt, fal-client 1.0.3):
inputs prompt, video_url, ref_image_urls, task, preprocess, resolution, match_input_num_frames,
match_input_frames_per_second, enable_auto_downsample; output {"video": {"url": ...}}.
Price: $0.04 (480p) to $0.08 (720p) per output second, counted at 16 fps. Local files are uploaded to
fal's CDN first; those URLs are public, so uploads are set to expire after 1 hour.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from godshot.cache import cached
from godshot.config import ConfigError, load_config

log = logging.getLogger(__name__)

MAX_REFS = 3  # actor/prop crops first; VACE conditions on a few reference images
RESOLUTIONS = ("240p", "360p", "480p", "580p", "720p")
TASKS = ("depth", "pose")
TIMEOUT_S = 900.0
UPLOAD_EXPIRY = "1h"


class WanError(RuntimeError):
    """fal / Wan VACE failed to return a video."""


def available() -> bool:
    try:
        import fal_client  # noqa: F401
    except ImportError:
        return False
    return True


def build_arguments(
    prompt: str, video_url: str, ref_urls: Sequence[str], resolution: str, task: str
) -> dict[str, Any]:
    """Request body for fal-ai/wan-vace-14b. Pure, so the exact payload is unit-tested."""
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}, got {resolution!r}")
    if task not in TASKS:
        raise ValueError(f"GODSHOT_WAN_TASK must be one of {TASKS}, got {task!r}")
    return {
        "prompt": prompt,
        "video_url": video_url,
        "ref_image_urls": list(ref_urls)[:MAX_REFS],
        "task": task,
        "preprocess": True,  # guide is RGB: let fal extract the depth / pose control from it
        "resolution": resolution,
        "aspect_ratio": "auto",
        "match_input_num_frames": True,  # same length and timing as the map-exact guide
        "match_input_frames_per_second": True,
        "enable_auto_downsample": True,  # guides above 241 frames are downsampled, then interpolated back
        "enable_safety_checker": True,
        "enable_prompt_expansion": False,
    }


def _client(cfg: Any) -> Any:
    if not cfg.FAL_KEY:
        raise ConfigError("FAL_KEY is not set. Add it to .env to use the wan_vace re-shoot backend.")
    try:
        import fal_client
    except ImportError as e:
        raise ConfigError("wan_vace backend needs `pip install -r requirements-wan.txt`.") from e
    return fal_client.SyncClient(key=cfg.FAL_KEY)


def _lifecycle() -> Any:
    """Uploads expire after UPLOAD_EXPIRY (fal CDN URLs are public)."""
    try:
        from fal_client.client import StorageSettings
    except ImportError:
        return None
    return StorageSettings(expires_in=UPLOAD_EXPIRY)


def _download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise WanError(f"unexpected video URL scheme: {url[:40]!r}")
    with urllib.request.urlopen(url, timeout=120) as r:  # https only, checked above
        return r.read()


def _call(
    client: Any,
    model: str,
    guide: Path,
    refs: Sequence[Path],
    prompt: str,
    resolution: str,
    task: str,
    download: Callable[[str], bytes] | None = None,
) -> bytes:
    t0 = time.perf_counter()
    life = _lifecycle()
    try:
        video_url = client.upload_file(guide, lifecycle=life)
        ref_urls = [client.upload_file(p, lifecycle=life) for p in list(refs)[:MAX_REFS]]
        args = build_arguments(prompt, video_url, ref_urls, resolution, task)
        result = client.subscribe(model, arguments=args, client_timeout=TIMEOUT_S)
    except ConfigError:
        raise
    except Exception as e:  # fal_client raises FalClientError / FalClientHTTPError / timeouts
        raise WanError(f"{type(e).__name__}: {e}") from e
    url = ((result or {}).get("video") or {}).get("url")
    if not url:
        raise WanError(f"no video in Wan VACE response (keys: {sorted((result or {}).keys())})")
    video = (download or _download)(url)
    log.info("Wan VACE %s: %.1f s, %d KB", model, time.perf_counter() - t0, len(video) // 1024)
    return video


def generate_video(
    refs: Sequence[Path],
    prompt: str,
    out: Path,
    guide: Path,
    resolution: str = "360p",
    client: Any | None = None,
) -> Path:
    """Guide video + reference frames + prompt → Wan VACE video at `out`.

    Cached by (guide + refs + model + prompt + params), like every other API call, so re-runs and the
    offline demo never call fal again.
    """
    cfg = load_config()
    guide, refs = Path(guide), [Path(r) for r in refs][:MAX_REFS]
    params = {"resolution": resolution, "task": cfg.WAN_TASK, "v": 1}
    key: list[bytes | str] = [
        guide.read_bytes(),
        *(r.read_bytes() for r in refs),
        cfg.MODEL_WAN,
        prompt,
        json.dumps(params, sort_keys=True),
    ]

    def call() -> bytes:
        c = client if client is not None else _client(cfg)
        return _call(c, cfg.MODEL_WAN, guide, refs, prompt, resolution, cfg.WAN_TASK)

    video = cached(key, call, kind="wan_vace")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(video)
    return out
