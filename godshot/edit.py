"""Apply map edits (drags / move presets) or a text instruction (Hydrogen) to a scene.

Spec: S09 (.claude/specs/S09*.md) — owner: Siva.

`apply_edit(scene, edit)` is pure and deterministic. Edit ops (dicts, `op` key):
- {"op": "move_camera_key", "index": i, "x": .., "y": ..}
- {"op": "set_yaw", "index": i, "yaw_deg": ..}
- {"op": "move_object", "id": "door_1", "x": .., "y": ..}
- {"op": "move_actor_key", "id": "a1", "index": i, "x": .., "y": ..}
- {"op": "set_fov", "hfov_deg": ..}
- {"op": "preset", "t0": .., "t1": .., "move": PRESET, "amount": float | None, "target": actor_id | None}
- {"op": "scene", "scene": {...}}  (replace with a full edited scene from the browser; validated)

Presets rebuild the camera track inside [t0, t1] (a keyframe every 0.5 s) and carry the rest of the
shot along rigidly, so the camera continues from where the new move ends.

`text_edit(scene, instruction)` asks MODEL_CHEAP (Hydrogen) to translate an instruction into preset
ops (small JSON), then applies them with `apply_edit`. Cached; offline → CacheMiss.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from godshot import geometry as g
from godshot.schema import CamKey, MotionSegment, SceneV1

log = logging.getLogger(__name__)

Preset = Literal[
    "static",
    "dolly_in",
    "dolly_out",
    "truck_left",
    "truck_right",
    "pan_left",
    "pan_right",
    "arc_left",
    "arc_right",
]
PRESETS: tuple[str, ...] = Preset.__args__  # type: ignore[attr-defined]

# Default amounts: room units for dolly/truck, degrees for pan/arc.
DEFAULT_AMOUNT = {"dolly": 1.5, "truck": 1.5, "pan": 30.0, "arc": 35.0}
KEY_STEP_S = 0.5


class EditError(ValueError):
    """Bad edit (unknown op, index out of range, unknown entity)."""


# --------------------------------------------------------------------------- helpers


def _keys(scene: SceneV1) -> list[CamKey]:
    return sorted(scene.camera.track, key=lambda k: k.t)


def _key(t: float, x: float, y: float, yaw: float) -> CamKey:
    return CamKey(t=round(t, 3), x=round(x, 4), y=round(y, 4), yaw_deg=round(g.wrap180(yaw), 3))


def _fwd_right(yaw_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    th = math.radians(yaw_deg)
    return (-math.sin(th), math.cos(th)), (math.cos(th), math.sin(th))


def _yaw_towards(cx: float, cy: float, tx: float, ty: float) -> float:
    return math.degrees(math.atan2(-(tx - cx), ty - cy))


def _arc_target(scene: SceneV1, t: float, target: str | None) -> tuple[float, float]:
    """Point to orbit: the named actor/object, else the first actor, else 4 units ahead of the camera."""
    eid = target or (scene.actors[0].id if scene.actors else None)
    if eid:
        try:
            e = scene.entity(eid)
        except KeyError as err:
            raise EditError(f"unknown arc target {eid!r}") from err
        if hasattr(e, "track"):
            x, y, _, _ = g.interp_actor(e, t)  # type: ignore[arg-type]
            return x, y
        return e.center[0], e.center[1]  # type: ignore[union-attr]
    cx, cy, yaw = g.interp_camera(scene, t)
    f, _ = _fwd_right(yaw)
    return cx + 4 * f[0], cy + 4 * f[1]


def _pose_at(
    move: str, start: tuple[float, float, float], f: float, amount: float, orbit: tuple[float, float]
):
    """Camera pose at fraction f ∈ [0, 1] of a preset move starting at `start`."""
    x0, y0, yaw0 = start
    fwd, right = _fwd_right(yaw0)
    if move == "static":
        return x0, y0, yaw0
    if move in ("dolly_in", "dolly_out"):
        d = amount * f * (1 if move == "dolly_in" else -1)
        return x0 + d * fwd[0], y0 + d * fwd[1], yaw0
    if move in ("truck_left", "truck_right"):
        d = amount * f * (1 if move == "truck_right" else -1)
        return x0 + d * right[0], y0 + d * right[1], yaw0
    if move in ("pan_left", "pan_right"):
        return x0, y0, yaw0 + amount * f * (1 if move == "pan_left" else -1)
    # arc: rotate the camera position around the orbit point; CCW from above = moving to its right
    tx, ty = orbit
    phi = math.radians(amount * f * (1 if move == "arc_right" else -1))
    rx, ry = x0 - tx, y0 - ty
    x = tx + rx * math.cos(phi) - ry * math.sin(phi)
    y = ty + rx * math.sin(phi) + ry * math.cos(phi)
    # keep the same framing offset the shot started with, turned by the orbit angle
    return x, y, yaw0 + math.degrees(phi)


def _segment_labels(move: str) -> tuple[str, str]:
    if move.startswith("pan_"):
        return "static", move
    return move, "none"


def _replace_segments(segs: list[MotionSegment], t0: float, t1: float, move: str) -> list[MotionSegment]:
    out: list[MotionSegment] = []
    for s in sorted(segs, key=lambda s: s.t0):
        if s.t1 <= t0 or s.t0 >= t1:
            out.append(s)
            continue
        if s.t0 < t0:
            out.append(s.model_copy(update={"t1": t0}))
        if s.t1 > t1:
            out.append(s.model_copy(update={"t0": t1}))
    tr, rot = _segment_labels(move)
    out.append(MotionSegment(t0=t0, t1=t1, translation=tr, rotation=rot))  # type: ignore[arg-type]
    return sorted(out, key=lambda s: s.t0)


def apply_preset(
    scene: SceneV1, t0: float, t1: float, move: str, amount: float | None = None, target: str | None = None
) -> SceneV1:
    """Rebuild the camera inside [t0, t1] as `move`; keys after t1 follow rigidly from the new end pose."""
    if move not in PRESETS:
        raise EditError(f"unknown move {move!r}; expected one of {PRESETS}")
    dur = scene.clip.duration_s
    t0, t1 = max(0.0, min(t0, dur)), max(0.0, min(t1, dur))
    if t1 - t0 < 0.2:
        raise EditError(f"segment too short: {t0}–{t1}")
    kind = move.split("_")[0]
    amt = float(amount) if amount is not None else DEFAULT_AMOUNT.get(kind, 0.0)
    start = g.interp_camera(scene, t0)
    old_end = g.interp_camera(scene, t1)
    orbit = _arc_target(scene, t0, target) if kind == "arc" else (0.0, 0.0)

    n = max(1, math.ceil((t1 - t0) / KEY_STEP_S))
    new_keys = [_key(t0 + (t1 - t0) * i / n, *_pose_at(move, start, i / n, amt, orbit)) for i in range(n + 1)]
    new_end = (new_keys[-1].x, new_keys[-1].y, new_keys[-1].yaw_deg)

    # rigid transform that maps the old end pose onto the new one, applied to everything after t1
    dyaw = g.wrap180(new_end[2] - old_end[2])
    c, s = math.cos(math.radians(dyaw)), math.sin(math.radians(dyaw))
    before = [k for k in _keys(scene) if k.t < t0 - 1e-6]
    after = []
    for k in _keys(scene):
        if k.t > t1 + 1e-6:
            rx, ry = k.x - old_end[0], k.y - old_end[1]
            after.append(
                _key(k.t, new_end[0] + rx * c - ry * s, new_end[1] + rx * s + ry * c, k.yaw_deg + dyaw)
            )

    cam = scene.camera.model_copy(
        update={
            "track": [*before, *new_keys, *after],
            "motion_segments": _replace_segments(scene.camera.motion_segments, t0, t1, move),
        }
    )
    return scene.model_copy(update={"camera": cam})


# --------------------------------------------------------------------------- apply_edit


def _index(n: int, i: Any) -> int:
    if not isinstance(i, int) or not 0 <= i < n:
        raise EditError(f"keyframe index {i!r} out of range 0..{n - 1}")
    return i


def apply_edit(scene: SceneV1, edit: dict[str, Any]) -> SceneV1:
    """Return a NEW scene with one edit applied (the input is never mutated)."""
    op = edit.get("op")
    s = scene.model_copy(deep=True)
    if op == "scene":
        return SceneV1.model_validate(edit["scene"])
    if op == "move_camera_key":
        keys = _keys(s)
        k = keys[_index(len(keys), edit.get("index"))]
        k.x, k.y = float(edit["x"]), float(edit["y"])
        s.camera.track = keys
        return s
    if op == "set_yaw":
        keys = _keys(s)
        keys[_index(len(keys), edit.get("index"))].yaw_deg = g.wrap180(float(edit["yaw_deg"]))
        s.camera.track = keys
        return s
    if op == "set_fov":
        s.camera.hfov_deg = min(120.0, max(10.0, float(edit["hfov_deg"])))
        return s
    if op == "move_object":
        for o in s.objects:
            if o.id == edit.get("id"):
                o.center = [float(edit["x"]), float(edit["y"])]
                return s
        raise EditError(f"unknown object {edit.get('id')!r}")
    if op == "move_actor_key":
        for a in s.actors:
            if a.id == edit.get("id"):
                track = sorted(a.track, key=lambda k: k.t)
                k = track[_index(len(track), edit.get("index"))]
                k.x, k.y = float(edit["x"]), float(edit["y"])
                a.track = track
                return s
        raise EditError(f"unknown actor {edit.get('id')!r}")
    if op == "preset":
        return apply_preset(
            s, float(edit["t0"]), float(edit["t1"]), str(edit["move"]), edit.get("amount"), edit.get("target")
        )
    raise EditError(f"unknown edit op {op!r}")


# --------------------------------------------------------------------------- text edit (Hydrogen)


class _PresetOp(BaseModel):
    move: Preset
    t0: float = Field(ge=0)
    t1: float = Field(ge=0)
    amount: float | None = None
    target: str | None = None


class _TextEditPlan(BaseModel):
    presets: list[_PresetOp] = Field(default_factory=list)
    hfov_deg: float | None = None
    note: str = ""


def _scene_brief(scene: SceneV1) -> str:
    segs = ", ".join(
        f"{s.t0:.1f}-{s.t1:.1f}s {s.translation}/{s.rotation}" for s in scene.camera.motion_segments
    )
    actors = "; ".join(f"{a.id} = {a.visual_description}" for a in scene.actors) or "none"
    objects = "; ".join(f"{o.id} = {o.visual_description}" for o in scene.objects) or "none"
    return (
        f"Shot duration: {scene.clip.duration_s:.1f} s. Lens hfov: {scene.camera.hfov_deg:.0f} deg.\n"
        f"Current camera moves: {segs or 'none listed'}.\nActors: {actors}.\nObjects: {objects}."
    )


TEXT_EDIT_PROMPT = """You edit the camera move of ONE continuous film shot on an overhead blocking map.
Translate the director's instruction into a JSON plan of preset camera moves. Output ONLY JSON.

{brief}

Allowed moves: static, dolly_in, dolly_out, truck_left, truck_right, pan_left, pan_right, arc_left, arc_right.
- arc_left / arc_right orbit a target actor or object (give its id in "target"); the camera keeps facing it.
- amount: room units for dolly/truck (1 unit ~ a doorway width, typical 0.5-3),
  degrees for pan/arc (typical 15-90).
  "slow" or "slight" means a small amount; "fast" or "big" a large one. Omit amount for a normal move.
- t0/t1 in seconds inside the shot. If the instruction gives no times, cover the whole shot.
- Set hfov_deg only if the instruction asks for a wider or tighter lens.
Instruction: {instruction}"""


def plan_text_edit(scene: SceneV1, instruction: str, client: Any | None = None) -> _TextEditPlan:
    """Hydrogen → preset plan (cached by model + prompt). VERIFY: thinking_level name on MODEL_CHEAP."""
    from godshot.cache import cached
    from godshot.config import get_client, load_config

    cfg = load_config()
    prompt = TEXT_EDIT_PROMPT.format(brief=_scene_brief(scene), instruction=instruction.strip())

    def call() -> dict[str, Any]:
        from google.genai import types

        cl = client or get_client()
        resp = cl.models.generate_content(
            model=cfg.MODEL_CHEAP,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=_TextEditPlan.model_json_schema(),
                thinking_config=types.ThinkingConfig(thinking_level="LOW"),
            ),
        )
        return json.loads(resp.text or "{}")

    raw = cached([cfg.MODEL_CHEAP, prompt, "text_edit.v1"], call, kind="text_edit")
    return _TextEditPlan.model_validate(raw)


def text_edit(scene: SceneV1, instruction: str, client: Any | None = None) -> SceneV1:
    """Apply a natural-language camera instruction via Hydrogen's preset plan."""
    plan = plan_text_edit(scene, instruction, client=client)
    out = scene
    if plan.hfov_deg is not None:
        out = apply_edit(out, {"op": "set_fov", "hfov_deg": plan.hfov_deg})
    for p in plan.presets:
        try:
            out = apply_preset(out, p.t0, p.t1, p.move, p.amount, p.target)
        except EditError as e:  # a bad step from the model shouldn't sink the whole edit
            log.warning("text_edit: skipped %s: %s", p.model_dump(), e)
    note = f"text edit: {instruction.strip()!r}" + (f" ({plan.note})" if plan.note else "")
    return out.model_copy(update={"model_notes": (out.model_notes + "\n" + note).strip()})
