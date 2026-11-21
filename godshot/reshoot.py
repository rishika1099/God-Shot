"""Omni re-shoot from an EDITED scene + conformance check against that edit.

Spec: S12 (.claude/specs/S12*.md) — owner: Prajesh.

Status: the Omni generation core (`generate_video`) and the map-exact guide video (`render_blockout`)
are built; `scripts/omni_spike.py` / `scripts/blockout_spike.py` use them.
`describe_shot`, `reshoot` and `conformance` wait on S06/S09 (and S02 for motion comparison).
"""

from __future__ import annotations

import base64
import json
import logging
import math
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from godshot import geometry
from godshot.cache import cached
from godshot.config import get_client, load_config
from godshot.schema import SceneV1, mmss

log = logging.getLogger(__name__)

AI_LABEL = "AI-generated re-shoot"
# Tasks whose output length + aspect come from the input video (verified for `edit`; VERIFY upscale).
_DURATION_FROM_INPUT = {"edit", "upscale"}
MAX_REFS = 6
OMNI_TIMEOUT_S = 300.0
OMNI_POLL_S = 5.0

# Interaction statuses (google-genai 3.6.1, types/interactions/interaction.py).
_DONE = {"completed"}
_FAILED = {"failed", "cancelled", "incomplete", "budget_exceeded", "requires_action"}


class OmniError(RuntimeError):
    """Omni generation failed, timed out, or returned no video."""


# --------------------------------------------------------------------------- Omni core


def _image_block(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")}


def _status(interaction: Any) -> str:
    return str(getattr(interaction, "status", "") or "").lower()


def _wait(client: Any, interaction: Any, timeout_s: float, poll_s: float) -> Any:
    deadline = time.monotonic() + timeout_s
    while _status(interaction) not in _DONE:
        status = _status(interaction)
        if status in _FAILED:
            errors = [
                f"{getattr(e, 'code', '?')}: {getattr(e, 'message', e)}"
                for e in getattr(interaction, "errors", None) or []
            ]
            raise OmniError(f"Omni interaction {interaction.id} ended with status={status}: {errors}")
        if time.monotonic() > deadline:
            raise OmniError(f"Omni interaction {interaction.id} not done after {timeout_s:.0f} s ({status})")
        time.sleep(poll_s)
        interaction = client.interactions.get(id=interaction.id)
    return interaction


def _video_bytes(client: Any, interaction: Any) -> bytes:
    """Pull the first video out of the interaction's model_output steps (inline data or a URI)."""
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        if getattr(step, "error", None):
            raise OmniError(f"Omni model_output error: {step.error}")
        for block in getattr(step, "content", None) or []:
            if getattr(block, "type", None) != "video":
                continue
            data = getattr(block, "data", None)
            if data:
                return data if isinstance(data, bytes) else base64.b64decode(data)
            uri = getattr(block, "uri", None)
            if uri:
                # VERIFY: files.download accepts a generated-video URI string.
                return client.files.download(file=uri)
    kinds = [
        (getattr(s, "type", None), [getattr(b, "type", None) for b in getattr(s, "content", None) or []])
        for s in getattr(interaction, "steps", None) or []
    ]
    raise OmniError(f"no video in Omni response (steps: {kinds})")


def _omni_call(
    refs: Sequence[bytes],
    prompt: str,
    params: dict[str, str],
    timeout_s: float,
    poll_s: float,
    guide: bytes | None = None,
    task: str | None = None,
) -> bytes:
    cfg = load_config()
    client = get_client()
    t0 = time.perf_counter()
    blocks: list[dict[str, Any]] = []
    if guide is not None:
        blocks.append(
            {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(guide).decode("ascii")}
        )
    blocks += [*(_image_block(r) for r in refs), {"type": "text", "text": prompt}]
    kwargs: dict[str, Any] = {}
    if task:
        kwargs["generation_config"] = {
            "video_config": {"task": task}
        }  # text_to_video|image_to_video|reference_to_video|edit|…
    interaction = client.interactions.create(
        model=cfg.MODEL_RESHOOT,
        input=blocks,
        response_format={"type": "video", "delivery": "inline", **params},
        background=True,
        **kwargs,
    )
    interaction = _wait(client, interaction, timeout_s, poll_s)
    video = _video_bytes(client, interaction)
    log.info("Omni %s: %.1f s, %d KB", cfg.MODEL_RESHOOT, time.perf_counter() - t0, len(video) // 1024)
    return video


def generate_video(
    refs: Sequence[bytes],
    prompt: str,
    out: Path,
    duration: str = "5s",
    resolution: str = "360p",
    aspect_ratio: str = "16:9",
    guide: bytes | None = None,
    task: str | None = None,
    timeout_s: float = OMNI_TIMEOUT_S,
    poll_s: float = OMNI_POLL_S,
) -> Path:
    """Reference JPEGs (≤ 6) + text [+ guide video] → Omni video saved to `out`.

    `guide`: an mp4 whose camera motion Omni should keep (e.g. `render_blockout` output) with
    `task="edit"`. Cached by (guide + refs + model + prompt + params + task).
    Generations take ~25–90 s; never call this live on stage (use the cache / pre-rendered files).
    """
    if not refs or len(refs) > MAX_REFS:
        raise ValueError(f"need 1..{MAX_REFS} reference images, got {len(refs)}")
    params = {"duration": duration, "resolution": resolution, "aspect_ratio": aspect_ratio}
    if task in _DURATION_FROM_INPUT:
        # API 400s: "Duration / Aspect ratio cannot be set in response format for edit task."
        params.pop("duration")
        params.pop("aspect_ratio")
    key: list[bytes | str] = [*refs, load_config().MODEL_RESHOOT, prompt, json.dumps(params, sort_keys=True)]
    if guide is not None or task:  # keep old cache keys valid for plain image→video calls
        key += [guide or b"", task or ""]
    video = cached(
        key, lambda: _omni_call(refs, prompt, params, timeout_s, poll_s, guide=guide, task=task), kind="omni"
    )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(video)
    return out


# --------------------------------------------------------------------------- blockout (guide video)

WALL_H = 2.6
CAM_H = 1.5
NEAR = 0.05
# Object heights (room units) by type: (z_bottom, z_top). Unknown types are 1 unit tall boxes.
OBJ_Z: dict[str, tuple[float, float]] = {
    "door": (0.0, 2.1), "window": (0.9, 2.1), "table": (0.0, 0.75), "desk": (0.0, 0.75),
    "chair": (0.0, 0.9), "sofa": (0.0, 0.8), "couch": (0.0, 0.8), "bed": (0.0, 0.6),
    "shelf": (0.0, 1.8), "bookshelf": (0.0, 1.8), "cabinet": (0.0, 1.6), "machine": (0.0, 2.0),
    "lamp": (0.0, 1.6), "counter": (0.0, 0.95),
}  # fmt: skip
ACTOR_BODY = (0.5, 0.35, 1.65)  # width, depth, height
ACTOR_HEAD = 0.28


def _hex(c: str, default: tuple[int, int, int] = (150, 150, 150)) -> tuple[int, int, int]:
    c = (c or "").lstrip("#")
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)) if len(c) == 6 else default
    except ValueError:
        return default


def _shade(rgb: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(v * k))) for v in rgb)  # type: ignore[return-value]


class _Cam:
    """Pinhole camera at (x, y, CAM_H) looking horizontally along yaw (same convention as geometry.py)."""

    def __init__(self, pose: tuple[float, float, float], hfov: float, w: int, h: int) -> None:
        x, y, yaw = pose
        r = math.radians(yaw)
        self.pos = (x, y, CAM_H)
        self.fwd = (-math.sin(r), math.cos(r), 0.0)
        self.right = (math.cos(r), math.sin(r), 0.0)
        self.w, self.h = w, h
        self.f = (w / 2) / math.tan(math.radians(hfov) / 2)

    def to_cam(self, p: tuple[float, float, float]) -> tuple[float, float, float]:
        d = (p[0] - self.pos[0], p[1] - self.pos[1], p[2] - self.pos[2])
        return (
            d[0] * self.right[0] + d[1] * self.right[1],
            d[2],
            d[0] * self.fwd[0] + d[1] * self.fwd[1],
        )

    def project(self, c: tuple[float, float, float]) -> tuple[float, float]:
        return (self.w / 2 + self.f * c[0] / c[2], self.h / 2 - self.f * c[1] / c[2])


def _clip_near(poly: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Sutherland–Hodgman against the plane z_cam = NEAR (keep the part in front of the camera)."""
    out: list[tuple[float, float, float]] = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        cin, pin = cur[2] >= NEAR, prev[2] >= NEAR
        if cin != pin:
            f = (NEAR - prev[2]) / (cur[2] - prev[2])
            out.append(tuple(prev[k] + f * (cur[k] - prev[k]) for k in range(3)))  # type: ignore[arg-type]
        if cin:
            out.append(cur)
    return out


def _box_faces(cx: float, cy: float, w: float, d: float, rot_deg: float, z0: float, z1: float):
    r = math.radians(rot_deg)
    ux, uy = math.cos(r), math.sin(r)  # local x axis
    vx, vy = -math.sin(r), math.cos(r)  # local y axis
    corners = [
        (cx + sx * w / 2 * ux + sy * d / 2 * vx, cy + sx * w / 2 * uy + sy * d / 2 * vy)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]
    faces = [
        [(*corners[i], z0), (*corners[(i + 1) % 4], z0), (*corners[(i + 1) % 4], z1), (*corners[i], z1)]
        for i in range(4)
    ]
    faces.append([(*c, z1) for c in corners])  # top
    return faces


def _blockout_frame(
    scene: SceneV1, t: float, w: int, h: int, colors: dict[str, str] | None = None
) -> Image.Image:
    cam = _Cam(geometry.interp_camera(scene, t), scene.camera.hfov_deg, w, h)
    img = Image.new("RGB", (w, h), (28, 30, 34))
    draw = ImageDraw.Draw(img)
    polys: list[tuple[float, list[tuple[float, float]], tuple[int, int, int], tuple[int, int, int]]] = []

    walls: list[tuple[float, list[tuple[float, float]], tuple[int, int, int], tuple[int, int, int]]] = []

    def add(world_poly, fill, outline=None, layer=None) -> None:
        cpoly = _clip_near([cam.to_cam(p) for p in world_poly])
        if len(cpoly) >= 3:
            depth = sum(c[2] for c in cpoly) / len(cpoly)
            target = polys if layer is None else layer
            target.append((depth, [cam.project(c) for c in cpoly], fill, outline or _shade(fill, 0.6)))

    outline = [tuple(p) for p in scene.room.outline] or [(-5, -1), (5, -1), (5, 10), (-5, 10)]
    # floor + 1-unit grid (drawn first, always behind everything else)
    floor = _clip_near([cam.to_cam((x, y, 0.0)) for x, y in outline])
    if len(floor) >= 3:
        draw.polygon([cam.project(c) for c in floor], fill=(70, 72, 78))
    xs, ys = [p[0] for p in outline], [p[1] for p in outline]
    for gx in range(math.floor(min(xs)), math.ceil(max(xs)) + 1):
        seg = _clip_near([cam.to_cam((gx, min(ys), 0.0)), cam.to_cam((gx, max(ys), 0.0))])
        if len(seg) == 2:
            draw.line([cam.project(c) for c in seg], fill=(92, 95, 102), width=1)
    for gy in range(math.floor(min(ys)), math.ceil(max(ys)) + 1):
        seg = _clip_near([cam.to_cam((min(xs), gy, 0.0)), cam.to_cam((max(xs), gy, 0.0))])
        if len(seg) == 2:
            draw.line([cam.project(c) for c in seg], fill=(92, 95, 102), width=1)
    # walls
    for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1], strict=True):
        add(
            [(x0, y0, 0.0), (x1, y1, 0.0), (x1, y1, WALL_H), (x0, y0, WALL_H)],
            (176, 178, 184),
            (120, 122, 128),
            layer=walls,
        )
    # objects
    for o in scene.objects:
        z0, z1 = OBJ_Z.get(o.type.lower(), (0.0, 1.0))
        base = _hex((colors or {}).get(o.id, o.color))
        for k, face in enumerate(
            _box_faces(
                o.center[0], o.center[1], max(o.size[0], 0.1), max(o.size[1], 0.1), o.rotation_deg, z0, z1
            )
        ):
            add(face, _shade(base, 1.15 if k == 4 else 0.85 + 0.05 * k))
    # actors: body box + head box, oriented by facing_deg
    for a in scene.actors:
        ax, ay, facing, visible = geometry.interp_actor(a, t)
        if not visible:
            continue
        base = _hex((colors or {}).get(a.id, a.color), (231, 76, 60))
        bw, bd, bh = ACTOR_BODY
        for k, face in enumerate(_box_faces(ax, ay, bw, bd, facing, 0.0, bh)):
            add(face, _shade(base, 1.1 if k == 4 else 0.8 + 0.06 * k))
        for face in _box_faces(ax, ay, ACTOR_HEAD, ACTOR_HEAD, facing, bh, bh + ACTOR_HEAD):
            add(face, (224, 190, 160))

    # The camera is inside the room, so every wall is behind every object: walls first, then objects
    # and actors by depth (painter's algorithm, far → near).
    for _, pts, fill, line in [*sorted(walls, key=lambda p: -p[0]), *sorted(polys, key=lambda p: -p[0])]:
        draw.polygon(pts, fill=fill, outline=line)
    return img


def render_blockout(
    scene: SceneV1,
    out: Path,
    fps: int = 24,
    size: tuple[int, int] = (640, 360),
    t0: float = 0.0,
    t1: float | None = None,
    colors: dict[str, str] | None = None,
) -> Path:
    """Grey-box previs of `scene` filmed along its EXACT camera track (x, y, yaw, hfov), as an mp4.

    Every frame comes straight from the scene numbers via the same pinhole convention as geometry.py,
    so this is the guide video that makes an Omni re-shoot follow the edited map.
    """
    t1 = scene.clip.duration_s if t1 is None else t1
    if t1 <= t0:
        raise ValueError(f"need t1 > t0, got {t0}..{t1}")
    w, h = size
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, round((t1 - t0) * fps))
    proc = subprocess.Popen(
        [
            shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )  # fmt: skip
    assert proc.stdin is not None
    try:
        for i in range(n):
            proc.stdin.write(_blockout_frame(scene, t0 + i / fps, w, h, colors).tobytes())
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode() if proc.stderr else ""
        if proc.wait() != 0:
            raise OmniError(f"ffmpeg failed writing blockout: {err.strip()[-300:]}")
    return out


# --------------------------------------------------------------------------- shot description (map → words)

# Named colours for the blockout ↔ prompt mapping ("the green box is the green wooden door").
_PALETTE: dict[str, str] = {
    "red": "#d62828", "orange": "#f77f00", "yellow": "#f6d02f", "green": "#2a9d3a", "blue": "#2364aa",
    "purple": "#7b2cbf", "pink": "#ff70a6", "cyan": "#1fb5c9", "brown": "#7f5539", "white": "#f1f1f1",
}  # fmt: skip
_NAMED: dict[str, tuple[int, int, int]] = {
    **{k: _hex(v) for k, v in _PALETTE.items()},
    "gold": (201, 162, 39), "grey": (128, 128, 128), "black": (25, 25, 25),
}  # fmt: skip


def color_name(hex_color: str) -> str:
    """Nearest plain colour name for a hex colour (used to tell Omni which box is which thing)."""
    rgb = _hex(hex_color)
    return min(_NAMED, key=lambda n: sum((a - b) ** 2 for a, b in zip(_NAMED[n], rgb, strict=True)))


def blockout_palette(scene: SceneV1) -> dict[str, str]:
    """entity id → blockout colour, keeping each entity's own colour unless two share a colour name."""
    used: set[str] = set()
    out: dict[str, str] = {}
    for e in [*scene.actors, *scene.objects]:
        name = color_name(e.color)
        if name in used or name in ("grey", "black"):  # grey/black vanish against the grey-box room
            name = next(n for n in _PALETTE if n not in used)
            out[e.id] = _PALETTE[name]
        else:
            out[e.id] = e.color
        used.add(name)
    return out


def _third(sx: float) -> str:
    return "on the left" if sx < 0.36 else "on the right" if sx > 0.64 else "in the centre"


def _lens(hfov: float) -> str:
    return "a wide-angle lens" if hfov >= 75 else "a long lens" if hfov < 45 else "a normal lens"


def _metres(d: float) -> str:
    return f"about {max(0.5, round(abs(d) * 2) / 2):g} m"


def _entities(scene: SceneV1, t: float) -> list[tuple[str, tuple[float, float], bool]]:
    rows = []
    for a in scene.actors:
        x, y, _, vis = geometry.interp_actor(a, t)
        rows.append((a.visual_description, (x, y), vis))
    rows += [(o.visual_description, (o.center[0], o.center[1]), True) for o in scene.objects]
    return rows


def _framing(scene: SceneV1, t: float, limit: int = 3) -> str:
    cam = geometry.interp_camera(scene, t)
    hfov = scene.camera.hfov_deg
    seen = [
        (
            abs(geometry.screen_x(cam, p, hfov) - 0.5),
            desc,
            geometry.screen_x(cam, p, hfov),
            geometry.distance(cam, p),
        )
        for desc, p, vis in _entities(scene, t)
        if vis and geometry.in_view(cam, p, hfov)
    ]
    if not seen:
        return "none of the named things are in frame"
    seen.sort()
    return "; ".join(f"{d} {_third(sx)}, {_metres(dist)} away" for _, d, sx, dist in seen[:limit])


def _subject(scene: SceneV1, t: float) -> str:
    cam = geometry.interp_camera(scene, t)
    best = min(
        _entities(scene, t)[: max(1, len(scene.actors))] if scene.actors else _entities(scene, t),
        key=lambda e: abs(geometry.bearing_deg(cam, e[1])),
    )
    return best[0]


def _segments(scene: SceneV1, dur: float) -> list[tuple[float, float, str, str]]:
    segs = [
        (m.t0, min(m.t1, dur), m.translation, m.rotation) for m in scene.camera.motion_segments if m.t0 < dur
    ]
    if not segs:  # no labelled segments: infer one from the track
        mo = geometry.camera_motion(scene, 0.0, dur)
        tr = "dolly_in" if mo["forward"] > 0.3 else "dolly_out" if mo["forward"] < -0.3 else "static"
        rot = "pan_left" if mo["dyaw"] > 8 else "pan_right" if mo["dyaw"] < -8 else "none"
        segs = [(0.0, dur, tr, rot)]
    return segs


def _move(scene: SceneV1, t0: float, t1: float, tr: str, rot: str) -> str:
    mo = geometry.camera_motion(scene, t0, t1)
    dyaw = mo["dyaw"]
    if tr in ("arc_left", "arc_right"):
        side = "left" if tr == "arc_left" else "right"
        who = _subject(scene, (t0 + t1) / 2)
        return f"orbits about {abs(dyaw):.0f}° to the {side} around {who}, keeping them centred"
    parts = []
    if tr in ("dolly_in", "dolly_out") or abs(mo["forward"]) > 0.3:
        parts.append(f"moves {'forward' if mo['forward'] >= 0 else 'backward'} {_metres(mo['forward'])}")
    if tr in ("truck_left", "truck_right") or abs(mo["lateral"]) > 0.3:
        parts.append(f"slides to the {'right' if mo['lateral'] >= 0 else 'left'} {_metres(mo['lateral'])}")
    if tr == "handheld_drift":
        parts.append("drifts gently, handheld")
    if rot != "none" or abs(dyaw) > 8:
        parts.append(f"pans {'left' if dyaw >= 0 else 'right'} about {abs(dyaw):.0f}°")
    return " and ".join(parts) if parts else "holds still"


def _enter_leave(scene: SceneV1, t0: float, t1: float) -> list[str]:
    hfov = scene.camera.hfov_deg
    c0, c1 = geometry.interp_camera(scene, t0), geometry.interp_camera(scene, t1)
    notes = []
    for (desc, p0, v0), (_, p1, v1) in zip(_entities(scene, t0), _entities(scene, t1), strict=True):
        was, now = v0 and geometry.in_view(c0, p0, hfov), v1 and geometry.in_view(c1, p1, hfov)
        if not was and now:
            edge = "left" if geometry.bearing_deg(c0, p0) > 0 else "right"
            notes.append(f"{desc} comes into view from the {edge} edge")
        elif was and not now:
            edge = "left" if geometry.bearing_deg(c1, p1) > 0 else "right"
            notes.append(f"{desc} leaves the frame on the {edge}")
    return notes


def _actor_motion(scene: SceneV1, dur: float) -> list[str]:
    _, _, yaw = geometry.interp_camera(scene, 0.0)
    th = math.radians(yaw)
    fwd, right = (-math.sin(th), math.cos(th)), (math.cos(th), math.sin(th))
    lines = []
    for a in scene.actors:
        x0, y0, _, _ = geometry.interp_actor(a, 0.0)
        x1, y1, _, _ = geometry.interp_actor(a, dur)
        dx, dy = x1 - x0, y1 - y0
        lat, fw = dx * right[0] + dy * right[1], dx * fwd[0] + dy * fwd[1]
        if math.hypot(lat, fw) < 0.4:
            lines.append(f"{a.visual_description} stays in place")
        elif abs(lat) >= abs(fw):
            lines.append(
                f"{a.visual_description} walks from {'left to right' if lat > 0 else 'right to left'}"
            )
        else:
            lines.append(f"{a.visual_description} walks {'away from' if fw > 0 else 'toward'} the camera")
    return lines


def shot_facts(scene: SceneV1, duration_s: float | None = None) -> dict[str, Any]:
    """Deterministic, geometry-derived facts about the shot (the source for every prompt)."""
    dur = min(duration_s or scene.clip.duration_s, scene.clip.duration_s)
    segs = [
        {"t0": a, "t1": b, "mmss0": mmss(a)[:5], "mmss1": mmss(b)[:5], "move": _move(scene, a, b, tr, rot),
         "events": _enter_leave(scene, a, b)}
        for a, b, tr, rot in _segments(scene, dur)
        if b > a
    ]  # fmt: skip
    return {
        "duration_s": round(dur, 2),
        "lens": _lens(scene.camera.hfov_deg),
        "start": _framing(scene, 0.0),
        "end": _framing(scene, dur),
        "segments": segs,
        "actors": _actor_motion(scene, dur),
    }


SHOT_RULES = (
    "One continuous shot, no cuts, no zoom. Keep the room, the people, their faces and their clothing "
    "identical to the reference images."
)


def describe_shot(scene_edited: SceneV1, duration_s: float | None = None, polish: bool = False) -> str:
    """Edited map → concrete shot description. Deterministic template from geometry (same map, same text).

    `polish=True` asks Hydrogen (MODEL_CHEAP) to smooth the wording without changing any fact (cached).
    """
    f = shot_facts(scene_edited, duration_s)
    lines = [f"A {f['duration_s']:g}-second shot on {f['lens']}.", f"At the start: {f['start']}."]
    for seg in f["segments"]:
        ev = f"; {'; '.join(seg['events'])}" if seg["events"] else ""
        lines.append(f"{seg['mmss0']}–{seg['mmss1']}: the camera {seg['move']}{ev}.")
    lines.append(f"At the end: {f['end']}.")
    if f["actors"]:
        lines.append(f"Throughout: {'; '.join(f['actors'])}.")
    lines.append(SHOT_RULES)
    text = "\n".join(lines)
    return _polish(text) if polish else text


def _polish(text: str) -> str:
    cfg = load_config()
    rules = (Path(__file__).parent / "prompts" / "shot_description.md").read_text(encoding="utf-8")
    prompt = f"{rules}\n\nSHOT DESCRIPTION:\n{text}"

    def call() -> str:
        from google.genai import types

        resp = get_client().models.generate_content(
            model=cfg.MODEL_CHEAP,
            contents=prompt,
            config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level="LOW")),
        )
        return (resp.text or "").strip() or text

    return cached([cfg.MODEL_CHEAP, prompt], call, kind="shot_description")


def edit_prompt(scene_edited: SceneV1, colors: dict[str, str], duration_s: float | None = None) -> str:
    """Prompt for Omni `edit`: which blockout shape is which real thing + the exact shot description."""
    actor_ids = {a.id for a in scene_edited.actors}
    legend = [
        f"the {color_name(colors.get(e.id, e.color))} {'figure' if e.id in actor_ids else 'box'}"
        f" is {e.visual_description}"
        for e in [*scene_edited.actors, *scene_edited.objects]
    ]
    return (
        "Re-render this grey-box previsualization as the real, photographic scene shown in the reference "
        f"images. In the previs: {'; '.join(legend)}. Keep the camera motion, timing, framing and every "
        "position EXACTLY as in the input video.\n" + describe_shot(scene_edited, duration_s)
    )


# --------------------------------------------------------------------------- reference images + label


def reference_images(clip: Path, scene_orig: SceneV1, out_dir: Path, max_refs: int = MAX_REFS) -> list[Path]:
    """≤ 6 refs: 3 keyframes from the ORIGINAL clip + crops around up to 3 entities (actors first).

    Crops are bounding-box guesses from geometry: a vertical strip centred on the entity's screen_x at a
    time when it is clearly in frame.
    """
    from godshot import ingest

    out_dir = Path(out_dir)
    info = ingest.probe(clip, check_length=False)
    dur = info.duration_s
    last = dur - 1.2 / info.fps
    keys = [ingest.frame_at(clip, min(f * dur, last), out_dir / "frames") for f in (0.1, 0.5, 0.9)]
    refs = list(keys)
    hfov = scene_orig.camera.hfov_deg
    span = min(dur, scene_orig.clip.duration_s)
    actor_ids = {a.id for a in scene_orig.actors}
    for e in [*scene_orig.actors, *scene_orig.objects]:
        if len(refs) >= max_refs:
            break
        for f in (0.5, 0.3, 0.7, 0.1, 0.9):
            t = f * span
            cam = geometry.interp_camera(scene_orig, t)
            if e.id in actor_ids:
                x, y, _, vis = geometry.interp_actor(e, t)
            else:
                x, y, vis = e.center[0], e.center[1], True
            sx = geometry.screen_x(cam, (x, y), hfov)
            if vis and 0.12 < sx < 0.88:
                frame = Image.open(ingest.frame_at(clip, min(t * dur / span, last), out_dir / "frames"))
                w, h = frame.size
                cx, half = int(sx * w), int(0.2 * w)
                crop = frame.crop((max(0, cx - half), 0, min(w, cx + half), h)).convert("RGB")
                path = out_dir / f"crop_{e.id}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                crop.save(path, quality=92)
                refs.append(path)
                break
    return refs[:max_refs]


def _label_png(text: str, out: Path, height: int) -> Path:
    from PIL import ImageFont

    size = max(12, height // 22)
    font = ImageFont.load_default(size=size)
    pad = size // 2
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    img = Image.new("RGBA", (right - left + 2 * pad, bottom - top + 2 * pad), (0, 0, 0, 160))
    ImageDraw.Draw(img).text((pad - left, pad - top), text, font=font, fill=(255, 255, 255, 255))
    img.save(out)
    return out


def burn_label(video: Path, out: Path, text: str = AI_LABEL) -> Path:
    """Overlay a visible "AI-generated re-shoot" badge (top-left). Pillow draws it; ffmpeg overlays it
    (this ffmpeg build has no drawtext)."""
    from godshot import ingest

    info = ingest.probe(video, check_length=False)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    png = _label_png(text, out.with_suffix(".label.png"), info.height)
    try:
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(video), "-i", str(png), "-filter_complex", "[0:v][1:v]overlay=10:10",
                "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "copy", str(out),
            ],
            check=True, capture_output=True,
        )  # fmt: skip
    finally:
        png.unlink(missing_ok=True)
    return out


# --------------------------------------------------------------------------- re-shoot


def _seconds(duration: str) -> float:
    return float(str(duration).rstrip("s"))


def work_dir_for(out: Path) -> Path:
    out = Path(out)
    return out.parent / f"{out.stem}_work"


def reshoot(
    clip: Path,
    scene_orig: SceneV1,
    scene_edited: SceneV1,
    out: Path,
    resolution: str = "360p",
    duration: str = "8s",
    mode: str = "blockout",
    polish: bool = False,
    take: int = 0,
    direction: tuple[Any, Any] | None = None,
) -> Path:
    """Edited map → new AI-generated shot that follows it. Returns the LABELLED mp4 at `out`.

    mode="blockout" (default): map-exact grey-box guide + refs → Omni `edit` (motion comes from the map).
    mode="text": refs + the geometry-derived description → Omni image/reference→video (fallback).
    `take` > 0 asks for a different sample of the same request (used by `reshoot_best`).
    `direction` = (SceneBrief, Direction) from S14 `direct.prepare`: the geometric prompt is kept verbatim and
    the director's performance notes + must-keep list are appended (S14, Akash; opt-in).
    Artifacts go to `<out stem>_work/` (refs, blockout.mp4, raw.mp4 = unlabelled, reshoot.json).
    """
    if mode not in ("blockout", "text"):
        raise ValueError(f"mode must be 'blockout' or 'text', got {mode!r}")
    work = work_dir_for(out)
    work.mkdir(parents=True, exist_ok=True)
    dur = min(scene_edited.clip.duration_s, _seconds(duration))
    refs = reference_images(Path(clip), scene_orig, work / "refs", max_refs=MAX_REFS)
    ref_bytes = [r.read_bytes() for r in refs]
    suffix = f"\n(Alternative take {take + 1}.)" if take else ""
    raw = work / "raw.mp4"
    if mode == "blockout":
        colors = blockout_palette(scene_edited)
        guide = render_blockout(scene_edited, work / "blockout.mp4", t1=dur, colors=colors)
        prompt = _directed(edit_prompt(scene_edited, colors, dur), direction) + suffix
        generate_video(ref_bytes, prompt, raw, resolution=resolution, guide=guide.read_bytes(), task="edit")
    else:
        prompt = _directed(describe_shot(scene_edited, dur, polish=polish), direction) + suffix
        generate_video(ref_bytes, prompt, raw, duration=f"{round(dur):d}s", resolution=resolution)
    out = burn_label(raw, Path(out))
    meta = {
        "label": AI_LABEL,
        "model": load_config().MODEL_RESHOOT,
        "mode": mode,
        "take": take,
        "directed": direction is not None,
        "duration_s": dur,
        "resolution": resolution,
        "prompt": prompt,
        "refs": [str(r) for r in refs],
        "raw": str(raw),
        "out": str(out),
    }
    (work / "reshoot.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return out


def _directed(prompt: str, direction: Any | None) -> str:
    """S14 (opt-in): geometric prompt + director's notes. `direction`: `direct.Pack` or (brief, dir)."""
    if direction is None:
        return prompt
    from godshot import direct  # S14 (lazy: direct imports shot_facts from here)

    return direct.compose(prompt, direction)


def reshoot_best(
    clip: Path,
    scene_orig: SceneV1,
    scene_edited: SceneV1,
    out_dir: Path,
    n: int = 3,
    workers: int = 3,
    direct: bool = False,
    score_preservation: bool = False,
    w_preserve: float = 0.4,
    request: str = "",
    critic_rounds: int = 0,
    **kwargs: Any,
) -> dict[str, Any]:
    """Generate `n` takes in parallel, score each with `conformance` vs the edited map, keep the best.

    S14 (opt-in; `request` or `critic_rounds` imply `direct`):
    - `direct=True`: scene analyst → edit interpreter (map diff + free-text `request`) → director, once
      (out_dir/direct/*.json); every take uses the directed prompt.
    - each take also gets preservation (LOCKED must-preserve items still visible) and, with a request,
      fulfilment (the requested changes happened); takes rank by (1 - w)·conformance + w·mean(scene scores).
    - `critic_rounds` > 0: the critic watches the best take; on "revise" one corrected take is generated and
      scored, up to that many rounds (out_dir/direct/critique_<r>.json).
    - `score_preservation=True` without `direct`: plain S12 takes, scored for preservation (for A/B).
    A take whose Omni call fails is retried once. Writes out_dir/reshoot_best.mp4 (labelled) + .json.
    """
    from concurrent.futures import ThreadPoolExecutor

    out_dir = Path(out_dir)
    direct = direct or bool(request.strip()) or critic_rounds > 0
    pack = brief = spec = None
    if direct or score_preservation:
        from godshot import direct as direct_mod  # S14

        dur = min(scene_edited.clip.duration_s, _seconds(kwargs.get("duration", "8s")))
        if direct:
            pack = direct_mod.prepare(clip, scene_orig, scene_edited, out_dir, duration_s=dur,
                                      request=request)  # fmt: skip
            brief, spec = pack.brief, pack.spec
        else:
            brief = direct_mod.analyze_scene(clip, scene_orig)

    def make(k: int, take_pack: Any) -> Path:
        out = out_dir / f"take_{k + 1}.mp4"
        try:
            return reshoot(clip, scene_orig, scene_edited, out, take=k, direction=take_pack, **kwargs)
        except OmniError as e:  # generic INVALID_ARGUMENT, more often on long prompts: retry once, compact
            log.warning("take %d failed (%s); retrying once with a compact prompt", k + 1, e)
            if take_pack is not None:
                from dataclasses import replace

                from godshot import direct as direct_mod  # S14

                take_pack = replace(take_pack, budget=direct_mod.COMPACT_PROMPT_CHARS)
            return reshoot(clip, scene_orig, scene_edited, out, take=k, direction=take_pack, **kwargs)

    def score(k: int, path: Path) -> dict[str, Any]:
        # score the UNLABELLED video (the badge could confuse the verifier)
        raw = work_dir_for(path) / "raw.mp4"
        conf = conformance(raw, scene_edited, work_dir=work_dir_for(path) / "conformance")
        row = {"take": k + 1, "video": str(path), "score": conf["score"], "hits": conf["hits"],
               "usable": conf["usable"], "conformance": conf}  # fmt: skip
        if brief is not None:
            from godshot import direct as direct_mod  # S14

            work = work_dir_for(path)
            pres = direct_mod.preservation(raw, brief, work_dir=work / "preservation", spec=spec)
            ful = direct_mod.fulfilment(raw, spec, work_dir=work / "fulfilment") if spec is not None else None
            row |= {
                "preservation": pres["score"], "kept": f"{pres['kept']}/{pres['n']}",
                "fulfilment": ful["score"] if ful else None,
                "done": f"{ful['kept']}/{ful['n']}" if ful else None,
                "combined": direct_mod.combined_score(conf["score"], pres["score"], w_preserve,
                                                      ful["score"] if ful else None),
                "preservation_items": pres["items"], "fulfilment_items": ful["items"] if ful else [],
                "_pres": pres, "_ful": ful,
            }  # fmt: skip
        return row

    def rank(r: dict[str, Any]) -> tuple[float, int]:
        return (r.get("combined", r["score"]), r["usable"])

    with ThreadPoolExecutor(max_workers=max(1, min(workers, n))) as pool:
        takes = list(pool.map(lambda k: score(k, make(k, pack)), range(n)))

    critiques = []
    if pack is not None and critic_rounds > 0:
        from dataclasses import replace

        from godshot import direct as direct_mod  # S14

        corrections: list[str] = []
        for r in range(critic_rounds):
            best = max(takes, key=rank)
            crit = direct_mod.critique(
                Path(best["video"]),
                replace(pack, corrections=list(corrections)),
                direct_mod.score_summary(best["conformance"], best.get("_pres"), best.get("_ful")),
            )
            (out_dir / "direct").mkdir(parents=True, exist_ok=True)
            (out_dir / "direct" / f"critique_{r + 1}.json").write_text(
                json.dumps({"take": best["take"], **crit.model_dump()}, indent=2) + "\n", encoding="utf-8"
            )
            critiques.append({"round": r + 1, "on_take": best["take"], "verdict": crit.verdict,
                              "issues": [i.model_dump() for i in crit.issues]})  # fmt: skip
            if crit.verdict == "accept" or not crit.corrections.strip():
                break
            corrections.append(crit.corrections.strip())
            k = n + r
            takes.append(score(k, make(k, replace(pack, corrections=list(corrections)))))

    best = max(takes, key=rank)
    final = out_dir / "reshoot_best.mp4"
    shutil.copyfile(best["video"], final)
    hidden = ("conformance", "preservation_items", "fulfilment_items", "_pres", "_ful")
    summary = {
        "label": AI_LABEL,
        "video": str(final),
        "best_take": best["take"],
        "score": best["score"],
        "hits": best["hits"],
        "usable": best["usable"],
        "takes": [{k: v for k, v in r.items() if k not in hidden} for r in takes],
        "checks": best["conformance"]["checks"],
    }
    if brief is not None:  # S14
        summary |= {"directed": direct, "request": request, "preservation": best["preservation"],
                    "kept": best["kept"], "fulfilment": best.get("fulfilment"), "done": best.get("done"),
                    "combined": best["combined"], "w_preserve": w_preserve,
                    "preservation_items": best["preservation_items"],
                    "fulfilment_items": best.get("fulfilment_items", []), "critiques": critiques}  # fmt: skip
    (out_dir / "reshoot_best.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return summary


def conformance(generated_clip: Path, scene_edited: SceneV1, work_dir: Path | None = None) -> dict[str, Any]:
    """How well a generated clip follows the EDITED map, scored on the shared-bank rule.

    checks = S04 make_checks(scene_edited) → frames from the generated clip (check times rescaled to its
    duration) → S05 verify (raw answers) → hit when S04 predict(scene_edited, c) == S05 observed(raw).
    Uncertain answers are not usable; an abstention (predict None) on a usable question is a miss.
    Given a labelled `reshoot()` output, it scores the sibling unlabelled `<stem>_work/raw.mp4`.
    """
    from godshot import checks as checks_mod  # S04
    from godshot import ingest  # S01
    from godshot import verify as verify_mod  # S05
    from godshot.schema import qkey

    generated_clip = Path(generated_clip)
    raw = work_dir_for(generated_clip) / "raw.mp4"
    if (
        raw.exists()
    ):  # a labelled reshoot() output: score the unlabelled video (the badge could confuse the verifier)
        generated_clip = raw
    work_dir = Path(work_dir) if work_dir else generated_clip.parent / f"{generated_clip.stem}_conformance"
    frames_dir = work_dir / "frames"
    gen = ingest.probe(generated_clip, check_length=False)
    scale = gen.duration_s / scene_edited.clip.duration_s

    bank = checks_mod.make_checks(scene_edited, frames_dir=frames_dir)
    raw_dir = work_dir / "raw_frames"
    for t in checks_mod.frames_needed(bank):  # extract at the rescaled time, store under the scene time
        target = frames_dir / ingest.frame_name(t)
        if not target.exists():
            src = ingest.frame_at(generated_clip, min(t * scale, gen.duration_s - 1.2 / gen.fps), raw_dir)
            frames_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)

    answers = verify_mod.verify(bank, frames_dir)
    rows, by_template = [], {}
    for c in bank:
        raw = answers.get(qkey(c))
        obs = verify_mod.observed(raw)
        pred = checks_mod.predict(scene_edited, c)
        usable, hit = obs is not None, obs is not None and pred == obs
        t = by_template.setdefault(c.template, {"usable": 0, "hits": 0})
        t["usable"] += usable
        t["hits"] += hit
        rows.append(
            {
                "check_id": c.check_id,
                "template": c.template,
                "mmss": c.mmss,
                "question": c.question.instructions,
                "predicted": pred,
                "observed": obs,
                "usable": usable,
                "hit": hit,
                "p_yes": getattr(raw, "p_yes", None),
                "choice_probs": getattr(raw, "choice_probs", None),
            }
        )
    usable_n = sum(r["usable"] for r in rows)
    hits_n = sum(r["hit"] for r in rows)
    result = {
        "score": round(hits_n / max(usable_n, 1), 3),
        "hits": hits_n,
        "usable": usable_n,
        "n_checks": len(rows),
        "by_template": by_template,
        "time_scale": round(scale, 4),
        "source": str(generated_clip),
        "checks": rows,
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "conformance.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return result
