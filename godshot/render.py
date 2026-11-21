"""scene.v1 → map.mp4 (overhead animation, frame-synced to the clip).

Spec: S07 (.claude/specs/S07-render.md) — owner: Akash.

Pillow, two layers per frame: a STATIC layer (background, grid, room, objects, ghost path) drawn
once at 2× and downscaled for antialiasing, and a DYNAMIC RGBA overlay (trails, actors, camera +
FOV wedge, low-confidence badges, header, progress bar) composited on top. Raw RGB frames are piped
to ffmpeg. World → screen: +x right, +y up; yaw 0 = up, + = counterclockwise (camera's left).
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from godshot import style
from godshot.geometry import interp_actor, interp_camera, object_points
from godshot.schema import Actor, Obj, SceneV1

log = logging.getLogger(__name__)

XY = tuple[float, float]
RGBA = tuple[int, int, int, int]
CAMERA_ID = "camera"  # low_confidence.entity_id that marks the camera


class RenderError(RuntimeError):
    """ffmpeg missing or failed."""


# --------------------------------------------------------------------------- view transform


@dataclass(frozen=True)
class View:
    """World (room units) → pixels. `k` scales everything for the supersampled static layer."""

    scale: float  # px per unit at k=1
    cx: float  # world point drawn at (ox, oy)
    cy: float
    ox: float
    oy: float

    def px(self, x: float, y: float, k: float = 1.0) -> XY:
        return (self.ox + (x - self.cx) * self.scale) * k, (self.oy - (y - self.cy) * self.scale) * k

    def pts(self, points: Iterable[Sequence[float]], k: float = 1.0) -> list[XY]:
        return [self.px(p[0], p[1], k) for p in points]


Box = tuple[float, float, float, float]


def _bbox(pts: Sequence[Sequence[float]]) -> Box:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _area(b: Box) -> float:
    return max(b[2] - b[0], 1e-6) * max(b[3] - b[1], 1e-6)


def scene_bounds(scene: SceneV1, ghost: SceneV1 | None = None) -> Box:
    """What to fit on screen: the ACTION (camera + actor tracks, and the ghost's camera path) padded by
    FOCUS_PAD, plus the room outline and objects only if they are at most ROOM_FIT_MAX_RATIO × that area.

    Models often invent a room far larger than where anything happens; fitting it shrinks actors to dots.
    """
    cams = [scene, *([ghost] if ghost else [])]
    core: list[Sequence[float]] = [(k.x, k.y) for s in cams for k in s.camera.track]
    core += [(k.x, k.y) for a in scene.actors for k in a.track]
    c = _bbox(core)
    p = style.FOCUS_PAD
    focus = (c[0] - p, c[1] - p, c[2] + p, c[3] + p)
    context = [*scene.room.outline, *(pt for o in scene.objects for pt in object_points(o))]
    if not context:
        return focus
    full = _bbox([*core, *context, focus[:2], focus[2:]])
    return full if _area(full) <= style.ROOM_FIT_MAX_RATIO * _area(focus) else focus


def fit_view(bounds: Box, size: tuple[int, int]) -> View:
    """Fit bounds + 10% margin into the area between header and footer, keeping the aspect ratio."""
    x0, y0, x1, y1 = bounds
    w, h = max(x1 - x0, style.MIN_FIT_SPAN), max(y1 - y0, style.MIN_FIT_SPAN)
    w, h = w * (1 + 2 * style.FIT_MARGIN), h * (1 + 2 * style.FIT_MARGIN)
    area_w, area_h = size[0], size[1] - style.HEADER_PX - style.FOOTER_PX
    scale = min(area_w / w, area_h / h)
    return View(scale, (x0 + x1) / 2, (y0 + y1) / 2, size[0] / 2, style.HEADER_PX + area_h / 2)


# --------------------------------------------------------------------------- shape helpers (world)


def heading(deg: float) -> XY:
    """Unit vector for a yaw/facing angle: 0 → +y, +90 → -x (turned left)."""
    r = math.radians(deg)
    return -math.sin(r), math.cos(r)


def _rot(p: XY, deg: float) -> XY:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return p[0] * c - p[1] * s, p[0] * s + p[1] * c


def rounded_rect(cx: float, cy: float, w: float, h: float, r: float, deg: float, n: int = 4) -> list[XY]:
    """Polygon of a rounded rectangle centred at (cx, cy), rotated CCW by `deg`."""
    r = min(r, w / 2, h / 2)
    out: list[XY] = []
    for i, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        ccx, ccy = sx * (w / 2 - r), sy * (h / 2 - r)
        for j in range(n + 1):
            a = math.radians(90 * i + 90 * j / n)
            out.append((ccx + r * math.cos(a), ccy + r * math.sin(a)))
    return [(cx + x, cy + y) for x, y in (_rot(p, deg) for p in out)]


def ellipse(cx: float, cy: float, w: float, d: float, deg: float, n: int = 24) -> list[XY]:
    """Ellipse `w` across × `d` deep, facing `deg` (the depth axis points along heading(deg))."""
    pts = [(w / 2 * math.cos(2 * math.pi * i / n), d / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return [(cx + x, cy + y) for x, y in (_rot(p, deg) for p in pts)]


def fov_wedge(x: float, y: float, yaw: float, hfov: float, length: float, n: int = 24) -> list[XY]:
    """Polygon of the horizontal FOV: apex at the camera, arc from yaw - hfov/2 to yaw + hfov/2."""
    arc = [heading(yaw - hfov / 2 + hfov * i / n) for i in range(n + 1)]
    return [(x, y), *((x + length * dx, y + length * dy) for dx, dy in arc)]


def _rgba(color: str, alpha: int = 255) -> RGBA:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha
    except ValueError:
        return (*_rgba(style.OBJ_FALLBACK)[:3], alpha)


def obj_color(o: Obj) -> str:
    if o.color and o.color.lower() != style.DEFAULT_OBJ_COLOR:
        return o.color
    return style.OBJ_PALETTE.get(o.type.lower(), style.OBJ_FALLBACK)


def _dashed(draw: ImageDraw.ImageDraw, pts: Sequence[XY], fill: RGBA, width: int, dash: float = 10.0) -> None:
    """Dashed closed polyline."""
    for a, b in zip(pts, [*pts[1:], pts[0]], strict=True):
        seg = math.dist(a, b)
        steps = max(1, int(seg / dash))
        for i in range(0, steps, 2):
            f0, f1 = i / steps, min(1.0, (i + 1) / steps)
            draw.line([(a[0] + (b[0] - a[0]) * f0, a[1] + (b[1] - a[1]) * f0),
                       (a[0] + (b[0] - a[0]) * f1, a[1] + (b[1] - a[1]) * f1)], fill=fill, width=width)


@lru_cache(maxsize=8)
def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # very old Pillow without FreeType default font
        return ImageFont.load_default()


# --------------------------------------------------------------------------- labels / state at t


def motion_label(scene: SceneV1, t: float) -> str:
    """'DOLLY IN · PAN LEFT' for the motion segment covering t ('STATIC' when nothing moves)."""
    seg = next((m for m in scene.camera.motion_segments if m.t0 <= t <= m.t1), None)
    if seg is None:
        return ""
    moving = [p for p in (seg.translation, seg.rotation) if p not in ("static", "none")]
    parts = [p.replace("_", " ").upper() for p in moving]
    return " · ".join(parts) or "STATIC"


def low_conf_ids(scene: SceneV1, t: float) -> set[str]:
    """Entity ids flagged low-confidence at time t."""
    return {lc.entity_id for lc in scene.low_confidence if lc.t0 <= t <= lc.t1}


def object_outline(o: Obj) -> list[XY]:
    if o.shape == "circle":
        return ellipse(o.center[0], o.center[1], o.size[0], o.size[0], 0.0)
    return rounded_rect(o.center[0], o.center[1], o.size[0], o.size[1], style.OBJ_CORNER, o.rotation_deg)


def short_label(entity_id: str) -> str:
    """'actor_ironman' → 'ironman': map labels drop the type prefix, unless only a number would remain
    ('actor_1' and 'obj_1' must stay distinguishable)."""
    for p in style.LABEL_PREFIXES:
        rest = entity_id[len(p):]
        if entity_id.lower().startswith(p) and rest and not rest.replace("_", "").isdigit():
            return rest
    return entity_id


def camera_path(scene: SceneV1) -> list[XY]:
    return [(k.x, k.y) for k in sorted(scene.camera.track, key=lambda k: k.t)]


class LabelPlacer:
    """Greedy label placement: try each candidate anchor, take the first that overlaps no earlier label."""

    def __init__(self) -> None:
        self.boxes: list[tuple[float, float, float, float]] = []

    @staticmethod
    def _hit(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    def place(
        self, d: ImageDraw.ImageDraw, text: str, candidates: Sequence[tuple[float, float, str]],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ) -> tuple[float, float, str]:
        boxes = [d.textbbox((x, y), text, font=font, anchor=anc) for x, y, anc in candidates]
        for cand, box in zip(candidates, boxes, strict=True):
            if not any(self._hit(box, b) for b in self.boxes):
                self.boxes.append(box)
                return cand
        self.boxes.append(boxes[0])  # everything collides: fall back to the first spot
        return candidates[0]

    def draw(
        self, d: ImageDraw.ImageDraw, text: str, candidates: Sequence[tuple[float, float, str]],
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont, fill: RGBA,
    ) -> None:
        x, y, anchor = self.place(d, text, candidates, font)
        d.text((x, y), text, fill=fill, font=font, anchor=anchor)


def _trail(points: Sequence[XY], draw: ImageDraw.ImageDraw, color: str, width: int) -> None:
    """Polyline fading from transparent (oldest) to TRAIL_ALPHA (newest)."""
    n = len(points)
    for i in range(1, n):
        alpha = int(style.TRAIL_ALPHA * i / (n - 1))
        draw.line([points[i - 1], points[i]], fill=_rgba(color, alpha), width=width)


def _sample_times(t: float, span: float) -> list[float]:
    t0 = max(0.0, t - span)
    n = style.TRAIL_SAMPLES
    return [t0 + (t - t0) * i / n for i in range(n + 1)] if t > t0 else []


# --------------------------------------------------------------------------- layers


def render_static(
    scene: SceneV1, view: View, size: tuple[int, int], ghost: SceneV1 | None = None
) -> Image.Image:
    """Background, grid, room, objects (+ labels) and ghost path. Drawn at SUPERSAMPLE× then downscaled."""
    k = style.SUPERSAMPLE
    img = Image.new("RGBA", (size[0] * k, size[1] * k), _rgba(style.BG))
    d = ImageDraw.Draw(img, "RGBA")

    # grid every GRID_STEP units, covering the whole canvas
    wx0, wy1 = view.cx - view.ox / view.scale, view.cy + view.oy / view.scale
    wx1, wy0 = view.cx + (size[0] - view.ox) / view.scale, view.cy - (size[1] - view.oy) / view.scale
    step = style.GRID_STEP
    for i in range(math.floor(wx0 / step), math.ceil(wx1 / step) + 1):
        x = view.px(i * step, 0, k)[0]
        d.line([(x, 0), (x, size[1] * k)], fill=_rgba(style.GRID), width=k)
    for j in range(math.floor(wy0 / step), math.ceil(wy1 / step) + 1):
        y = view.px(0, j * step, k)[1]
        d.line([(0, y), (size[0] * k, y)], fill=_rgba(style.GRID), width=k)

    if len(scene.room.outline) >= 2:
        pts = view.pts(scene.room.outline, k)
        d.line([*pts, pts[0]], fill=_rgba(style.ROOM), width=style.ROOM_WIDTH_PX * k, joint="curve")

    if ghost is not None and len(ghost.camera.track) >= 2:
        gpts = view.pts([(c.x, c.y) for c in sorted(ghost.camera.track, key=lambda c: c.t)], k)
        ghost_fill = _rgba(style.GHOST, style.GHOST_ALPHA)
        d.line(gpts, fill=ghost_fill, width=style.TRAIL_WIDTH_PX * k, joint="curve")

    # the camera's whole path, always visible (faint), with a ring at the start position
    path = camera_path(scene)
    if len(path) >= 2:
        cpts = view.pts(path, k)
        d.line(cpts, fill=_rgba(style.CAMERA, style.CAM_PATH_ALPHA), width=style.TRAIL_WIDTH_PX * k // 2 + k,
               joint="curve")
        r0 = 7 * k
        d.ellipse([cpts[0][0] - r0, cpts[0][1] - r0, cpts[0][0] + r0, cpts[0][1] + r0],
                  outline=_rgba(style.CAMERA, 160), width=2 * k)

    font = _font(style.SMALL_FONT_PX * k)
    placer = LabelPlacer()
    for o in scene.objects:
        poly = view.pts(object_outline(o), k)
        d.polygon(poly, fill=_rgba(obj_color(o), style.OBJ_ALPHA))
        lx = view.px(o.center[0], o.center[1], k)[0]
        ys = [p[1] for p in poly]
        xs = [p[0] for p in poly]
        my = (min(ys) + max(ys)) / 2
        candidates = [(lx, max(ys) + 4 * k, "mt"), (lx, min(ys) - 4 * k, "mb"),
                      (max(xs) + 6 * k, my, "lm"), (min(xs) - 6 * k, my, "rm")]
        placer.draw(d, short_label(o.id), candidates, font, _rgba(style.TEXT_DIM))

    return img.resize(size, Image.Resampling.LANCZOS)


def _draw_actor(
    d: ImageDraw.ImageDraw, view: View, a: Actor, t: float, low: bool, placer: LabelPlacer
) -> None:
    trail = [p for p in (interp_actor(a, s) for s in _sample_times(t, style.ACTOR_TRAIL_S)) if p[3]]
    _trail(view.pts(trail), d, a.color, style.TRAIL_WIDTH_PX)
    x, y, facing, visible = interp_actor(a, t)
    if not visible:
        return
    # never smaller than ACTOR_MIN_SHOULDER_PX on screen, however far the view is zoomed out
    grow = max(1.0, style.ACTOR_MIN_SHOULDER_PX / (style.ACTOR_SHOULDER[0] * view.scale))
    sw, sd = style.ACTOR_SHOULDER[0] * grow, style.ACTOR_SHOULDER[1] * grow
    body = view.pts(ellipse(x, y, sw, sd, facing))
    d.polygon(body, fill=_rgba(a.color, 230))
    hx, hy = view.px(x, y)
    hr = style.ACTOR_HEAD_R * grow * view.scale
    d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=_rgba(a.color), outline=_rgba(style.BG), width=2)
    fx, fy = heading(facing)
    nose = view.px(x + fx * (sd / 2 + 0.12 * grow), y + fy * (sd / 2 + 0.12 * grow))
    d.line([(hx, hy), nose], fill=_rgba(style.TEXT), width=3)
    off = hr + sd * view.scale / 2 + 4
    candidates = [(hx, hy + off, "mt"), (hx, hy - off, "mb"), (hx + off, hy, "lm"), (hx - off, hy, "rm")]
    placer.draw(d, short_label(a.id), candidates, _font(style.SMALL_FONT_PX), _rgba(style.TEXT))
    if low:
        _dashed(d, view.pts(ellipse(x, y, sw * 1.5, sw * 1.5, 0)), _rgba(style.LOWCONF), 3)
        _badge(d, view.px(x + sw * 0.75, y + sw * 0.75))


def _draw_camera(d: ImageDraw.ImageDraw, view: View, scene: SceneV1, t: float, low: bool) -> None:
    trail = [interp_camera(scene, s)[:2] for s in _sample_times(t, style.CAM_TRAIL_S)]
    _trail(view.pts(trail), d, style.CAMERA, style.TRAIL_WIDTH_PX)
    x, y, yaw = interp_camera(scene, t)
    alpha = int(style.FOV_ALPHA * (style.FOV_LOWCONF_FACTOR if low else 1.0))
    wedge = view.pts(fov_wedge(x, y, yaw, scene.camera.hfov_deg, style.FOV_LENGTH))
    d.polygon(wedge, fill=_rgba(style.CAMERA, alpha))
    if low:  # dimmed fill alone is easy to miss on the dark map → dashed amber outline too
        _dashed(d, wedge, _rgba(style.LOWCONF), 3, dash=14)
    else:
        d.line([*wedge[1:2], wedge[0], wedge[-1]], fill=_rgba(style.CAMERA, min(255, alpha * 2)), width=2)
    body = view.pts(rounded_rect(x, y, style.CAM_SIZE, style.CAM_SIZE, style.CAM_SIZE * 0.25, yaw))
    d.polygon(body, fill=_rgba(style.CAMERA), outline=_rgba(style.BG))
    fx, fy = heading(yaw)
    lens = view.px(x + fx * style.CAM_SIZE * 0.6, y + fy * style.CAM_SIZE * 0.6)
    d.line([view.px(x, y), lens], fill=_rgba(style.BG), width=4)
    if low:
        _badge(d, view.px(x + style.CAM_SIZE, y + style.CAM_SIZE))


def _badge(d: ImageDraw.ImageDraw, at: XY, r: float = style.BADGE_R_PX) -> None:
    d.ellipse([at[0] - r, at[1] - r, at[0] + r, at[1] + r], fill=_rgba(style.LOWCONF))
    d.text(at, "?", fill=_rgba(style.BG), font=_font(int(r * 1.6)), anchor="mm")


def render_overlay(scene: SceneV1, view: View, size: tuple[int, int], t: float) -> Image.Image:
    """Everything that moves at time t, on a transparent RGBA layer."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    low = low_conf_ids(scene, t)

    for o in scene.objects:
        if o.id in low:
            poly = view.pts(object_outline(o))
            _dashed(d, poly, _rgba(style.LOWCONF), 3)
            _badge(d, (max(p[0] for p in poly), min(p[1] for p in poly)))
    placer = LabelPlacer()
    for a in scene.actors:
        _draw_actor(d, view, a, t, a.id in low, placer)
    _draw_camera(d, view, scene, t, CAMERA_ID in low)

    # header: motion label (left), timestamp (right)
    d.rectangle([0, 0, size[0], style.HEADER_PX], fill=_rgba(style.BG, 220))
    d.text((24, style.HEADER_PX / 2), motion_label(scene, t), fill=_rgba(style.TEXT),
           font=_font(style.LABEL_FONT_PX), anchor="lm")
    d.text((size[0] - 24, style.HEADER_PX / 2), f"{t:05.2f}s", fill=_rgba(style.TEXT_DIM),
           font=_font(style.SMALL_FONT_PX), anchor="rm")

    # progress bar
    y0 = size[1] - style.FOOTER_PX / 2 - style.PROGRESS_H_PX / 2
    y1 = y0 + style.PROGRESS_H_PX
    d.rectangle([24, y0, size[0] - 24, y1], fill=_rgba(style.PROGRESS_BG))
    frac = min(1.0, max(0.0, t / scene.clip.duration_s))
    d.rectangle([24, y0, 24 + (size[0] - 48) * frac, y1], fill=_rgba(style.PROGRESS_FG))
    return img


def render_frame(scene: SceneV1, t: float, size: tuple[int, int] = (1080, 960), ghost: SceneV1 | None = None,
                 _static: tuple[View, Image.Image] | None = None) -> Image.Image:
    """One RGB frame at time t (for tests, thumbnails and the web UI)."""
    view, static = _static or _prepare(scene, size, ghost)
    return Image.alpha_composite(static, render_overlay(scene, view, size, t)).convert("RGB")


def _prepare(scene: SceneV1, size: tuple[int, int], ghost: SceneV1 | None) -> tuple[View, Image.Image]:
    view = fit_view(scene_bounds(scene, ghost), size)
    return view, render_static(scene, view, size, ghost)


# --------------------------------------------------------------------------- video


def frame_count(scene: SceneV1, fps: float | None = None) -> int:
    return round(scene.clip.duration_s * (fps or scene.clip.fps))


def _ffmpeg_cmd(out: Path, size: tuple[int, int], fps: float) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RenderError("ffmpeg not found on PATH. Install ffmpeg (macOS: `brew install ffmpeg`).")
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", f"{fps}", "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ]  # fmt: skip


def render_map(
    scene: SceneV1,
    out: Path,
    fps: float | None = None,
    size: tuple[int, int] = (1080, 960),
    ghost: SceneV1 | None = None,
) -> Path:
    """Render `round(duration * fps)` frames at the clip's fps (default) to an H.264 mp4."""
    if size[0] % 2 or size[1] % 2:
        raise ValueError(f"size must be even for yuv420p, got {size}")
    fps = fps or scene.clip.fps
    n = frame_count(scene, fps)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    prepared = _prepare(scene, size, ghost)

    t0 = time.perf_counter()
    proc = subprocess.Popen(_ffmpeg_cmd(out, size, fps), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for i in range(n):
            proc.stdin.write(render_frame(scene, i / fps, size, ghost, prepared).tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass  # ffmpeg died; its stderr is reported below
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RenderError(f"ffmpeg failed ({proc.returncode}): {err.strip()[-500:]}")
    dt = time.perf_counter() - t0
    log.info("rendered %s: %d frames in %.1f s (%.0f fps)", out, n, dt, n / dt if dt else 0)
    return out
