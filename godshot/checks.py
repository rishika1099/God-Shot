"""scene.v1 → check.v1 list from geometry templates (code-generated, never LLM).

Spec: S04 (.claude/specs/S04*.md) — owner: Rishika.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from godshot import geometry as _geom
from godshot.schema import (
    Actor,
    CheckV1,
    InputRef,
    NoulCriteria,
    Obj,
    QKey,
    Question,
    SceneV1,
    Template,
    make_check_id,
    mmss,
    qkey,
)

log = logging.getLogger(__name__)

# Abstain margins (S04 spec; shared between make_checks and predict so they cannot drift).
VISIBLE_MARGIN_DEG = 8.0
SIDE_MARGIN_DEG = 6.0
ORDER_MARGIN_DEG = 8.0
CLOSER_MIN_RATIO = 1.3
TOWARD_MIN_REL_CHANGE = 0.15
PAN_MIN_DYAW_DEG = 10.0
DOLLY_MIN_REL_DIST = 0.15

MAX_CHECKS = 60
PAIR_DT = 1.5
CONTROL_STRIDE = 5  # ~20% of SIDE checks get a mirrored opposite-side control check


# --------------------------------------------------------------------------- geometry bridge
# Calls godshot.geometry (S03, Siva) whenever implemented; falls back to the exact S03 formulas
# while S03 is still unmerged on main so S04/S06 stay independently testable.


def _wrap180(a: float) -> float:
    try:
        return float(_geom.wrap180(a))
    except NotImplementedError:
        w = (a + 180.0) % 360.0 - 180.0
        return 180.0 if w == -180.0 else w


def _interp_camera(scene: SceneV1, t: float) -> tuple[float, float, float]:
    try:
        cx, cy, yaw = _geom.interp_camera(scene, t)
        return float(cx), float(cy), float(yaw)
    except NotImplementedError:
        keys = sorted(scene.camera.track, key=lambda k: k.t)
        if t <= keys[0].t:
            return keys[0].x, keys[0].y, keys[0].yaw_deg
        if t >= keys[-1].t:
            return keys[-1].x, keys[-1].y, keys[-1].yaw_deg
        for k0, k1 in itertools.pairwise(keys):
            if k0.t <= t <= k1.t:
                dt = k1.t - k0.t
                alpha = 0.0 if dt <= 0 else (t - k0.t) / dt
                x = k0.x + alpha * (k1.x - k0.x)
                y = k0.y + alpha * (k1.y - k0.y)
                yaw = _wrap180(k0.yaw_deg + alpha * _wrap180(k1.yaw_deg - k0.yaw_deg))
                return x, y, yaw
        return keys[-1].x, keys[-1].y, keys[-1].yaw_deg


def _interp_actor(actor: Actor, t: float) -> tuple[float, float, float, bool]:
    try:
        x, y, facing, vis = _geom.interp_actor(actor, t)
        return float(x), float(y), float(facing), bool(vis)
    except NotImplementedError:
        keys = sorted(actor.track, key=lambda k: k.t)
        if t <= keys[0].t:
            return keys[0].x, keys[0].y, keys[0].facing_deg, keys[0].visible
        if t >= keys[-1].t:
            return keys[-1].x, keys[-1].y, keys[-1].facing_deg, keys[-1].visible
        for k0, k1 in itertools.pairwise(keys):
            if k0.t <= t <= k1.t:
                dt = k1.t - k0.t
                alpha = 0.0 if dt <= 0 else (t - k0.t) / dt
                x = k0.x + alpha * (k1.x - k0.x)
                y = k0.y + alpha * (k1.y - k0.y)
                facing = _wrap180(k0.facing_deg + alpha * _wrap180(k1.facing_deg - k0.facing_deg))
                vis = k0.visible if alpha < 0.5 else k1.visible
                return x, y, facing, vis
        return keys[-1].x, keys[-1].y, keys[-1].facing_deg, keys[-1].visible


def _bearing_deg(cam: tuple[float, float, float], p: tuple[float, float]) -> float:
    try:
        return float(_geom.bearing_deg(cam, p))
    except NotImplementedError:
        cx, cy, yaw = cam
        px, py = p
        raw = math.degrees(math.atan2(-(px - cx), py - cy))
        return _wrap180(raw - yaw)


def _in_view(cam: tuple[float, float, float], p: tuple[float, float], hfov: float) -> bool:
    try:
        return bool(_geom.in_view(cam, p, hfov))
    except NotImplementedError:
        return abs(_bearing_deg(cam, p)) < hfov / 2.0


def _distance(cam: tuple[float, float, float], p: tuple[float, float]) -> float:
    try:
        return float(_geom.distance(cam, p))
    except NotImplementedError:
        return math.hypot(p[0] - cam[0], p[1] - cam[1])


def _camera_motion(scene: SceneV1, t0: float, t1: float) -> dict[str, float]:
    try:
        res = _geom.camera_motion(scene, t0, t1)
        return {k: float(v) for k, v in res.items()}
    except NotImplementedError:
        c0 = _interp_camera(scene, t0)
        c1 = _interp_camera(scene, t1)
        dx = c1[0] - c0[0]
        dy = c1[1] - c0[1]
        yaw0 = math.radians(c0[2])
        # Camera forward unit vector at yaw0 (0 -> +y, +90 -> -x): (-sin(yaw0), cos(yaw0))
        # Camera right unit vector at yaw0 (0 -> +x, +90 -> +y): (cos(yaw0), sin(yaw0))
        forward = -math.sin(yaw0) * dx + math.cos(yaw0) * dy
        lateral = math.cos(yaw0) * dx + math.sin(yaw0) * dy
        dyaw = _wrap180(c1[2] - c0[2])

        dists: list[float] = []
        for obj in scene.objects:
            dists.append(_distance(c0, (obj.center[0], obj.center[1])))
        for actor in scene.actors:
            ax, ay, _, vis = _interp_actor(actor, t0)
            if vis:
                dists.append(_distance(c0, (ax, ay)))
        mean_dist = sum(dists) / len(dists) if dists else 5.0
        return {
            "forward": forward,
            "lateral": lateral,
            "dyaw": dyaw,
            "mean_scene_dist": mean_dist,
        }


# --------------------------------------------------------------------------- shared prediction core


def _frame_path(frames_dir: Path, t: float) -> str:
    ms = max(0, round(t * 1000))
    return (frames_dir / f"{ms:06d}.jpg").as_posix()


def _entity_pose(scene: SceneV1, entity_id: str, t: float) -> tuple[tuple[float, float], bool] | None:
    """Return ((x, y), is_actor_visible) for an entity, or None if missing from scene."""
    for obj in scene.objects:
        if obj.id == entity_id:
            return (obj.center[0], obj.center[1]), True
    for actor in scene.actors:
        if actor.id == entity_id:
            ax, ay, _, vis = _interp_actor(actor, t)
            return (ax, ay), vis
    return None


def _nearest_abs_bearing(
    scene: SceneV1, entity_id: str, cam: tuple[float, float, float], p: tuple[float, float]
) -> float:
    """|bearing| of the part of the entity nearest the optical axis.

    The visibility question asks whether an entity is visible "even partly", so a large object whose
    centre is just outside the frame but whose edge is inside counts as visible. Objects use their
    outline (S03 `object_bearing_range`); actors are points.
    """
    for obj in scene.objects:
        if obj.id == entity_id:
            try:
                lo, hi = _geom.object_bearing_range(cam, obj)
            except NotImplementedError:
                break
            return 0.0 if lo <= 0.0 <= hi else min(abs(lo), abs(hi))
    return abs(_bearing_deg(cam, p))


def _evaluate(
    scene: SceneV1,
    template: Template,
    entities: Sequence[str],
    t: float,
    t_end: float | None = None,
) -> tuple[bool | str | None, float | None]:
    """Single source of truth for geometry evaluation and abstain margins.

    Returns `(prediction, margin)` where `prediction` is None on abstain / missing / invisible.
    Never returns `"other"`.
    """
    hfov = scene.camera.hfov_deg
    half_fov = hfov / 2.0

    if template in ("VISIBLE", "NOT_VISIBLE"):
        if len(entities) != 1:
            return None, None
        pose = _entity_pose(scene, entities[0], t)
        if pose is None:
            return None, None
        p, visible_flag = pose
        if not visible_flag:
            # The scene says the actor cannot be seen: that is a claim ("not visible"), not an abstention.
            return False, None
        cam = _interp_camera(scene, t)
        b = _nearest_abs_bearing(scene, entities[0], cam, p)
        if template == "VISIBLE":
            if b < half_fov - VISIBLE_MARGIN_DEG:
                return True, round(half_fov - b, 1)
            if b > half_fov + VISIBLE_MARGIN_DEG:
                return False, round(b - half_fov, 1)
            return None, None
        # NOT_VISIBLE: outside hfov + 8° is a prediction (False), not an abstention
        if b > half_fov + VISIBLE_MARGIN_DEG:
            return False, round(b - half_fov, 1)
        if b < half_fov - VISIBLE_MARGIN_DEG:
            return True, round(half_fov - b, 1)
        return None, None

    if template in ("SIDE_LEFT", "SIDE_RIGHT"):
        if len(entities) != 1:
            return None, None
        pose = _entity_pose(scene, entities[0], t)
        if pose is None:
            return None, None
        p, visible_flag = pose
        if not visible_flag:
            return None, None
        cam = _interp_camera(scene, t)
        if not _in_view(cam, p, hfov):
            return None, None
        b = _bearing_deg(cam, p)
        if abs(b) <= SIDE_MARGIN_DEG:
            return None, None
        is_left = b > 0.0
        pred = is_left if template == "SIDE_LEFT" else (not is_left)
        return pred, round(abs(b), 1)

    if template == "A_LEFT_OF_B":
        if len(entities) != 2:
            return None, None
        pose_a = _entity_pose(scene, entities[0], t)
        pose_b = _entity_pose(scene, entities[1], t)
        if pose_a is None or pose_b is None:
            return None, None
        pa, vis_a = pose_a
        pb, vis_b = pose_b
        if not (vis_a and vis_b):
            return None, None
        cam = _interp_camera(scene, t)
        if not (_in_view(cam, pa, hfov) and _in_view(cam, pb, hfov)):
            return None, None
        gap = _bearing_deg(cam, pa) - _bearing_deg(cam, pb)
        if abs(gap) <= ORDER_MARGIN_DEG:
            return None, None
        return (gap > 0.0), round(abs(gap), 1)

    if template == "CLOSER":
        if len(entities) != 2:
            return None, None
        pose_a = _entity_pose(scene, entities[0], t)
        pose_b = _entity_pose(scene, entities[1], t)
        if pose_a is None or pose_b is None:
            return None, None
        pa, vis_a = pose_a
        pb, vis_b = pose_b
        if not (vis_a and vis_b):
            return None, None
        cam = _interp_camera(scene, t)
        if not (_in_view(cam, pa, hfov) and _in_view(cam, pb, hfov)):
            return None, None
        da = _distance(cam, pa)
        db = _distance(cam, pb)
        if da <= 1e-6 or db <= 1e-6:
            return None, None
        ratio = max(da, db) / min(da, db)
        if ratio <= CLOSER_MIN_RATIO:
            return None, None
        return ("a_first" if da < db else "b_second"), round(ratio, 2)

    if template == "TOWARD":
        if len(entities) != 1 or t_end is None:
            return None, None
        pose0 = _entity_pose(scene, entities[0], t)
        pose1 = _entity_pose(scene, entities[0], t_end)
        if pose0 is None or pose1 is None:
            return None, None
        p0, vis0 = pose0
        p1, vis1 = pose1
        if not (vis0 and vis1):
            return None, None
        cam0 = _interp_camera(scene, t)
        cam1 = _interp_camera(scene, t_end)
        if not (_in_view(cam0, p0, hfov) and _in_view(cam1, p1, hfov)):
            return None, None
        d0 = _distance(cam0, p0)
        d1 = _distance(cam1, p1)
        if d0 <= 1e-6:
            return None, None
        rel = (d0 - d1) / d0  # positive => got closer
        if abs(rel) <= TOWARD_MIN_REL_CHANGE:
            return None, None
        return (rel > 0.0), round(abs(rel) * 100.0, 1)

    if template == "PAN":
        if t_end is None:
            return None, None
        motion = _camera_motion(scene, t, t_end)
        dyaw = motion["dyaw"]
        if abs(dyaw) <= PAN_MIN_DYAW_DEG:
            return None, None
        return ("pan_left" if dyaw > 0.0 else "pan_right"), round(abs(dyaw), 1)

    if template == "DOLLY":
        if t_end is None:
            return None, None
        motion = _camera_motion(scene, t, t_end)
        fwd = motion["forward"]
        mean_d = motion["mean_scene_dist"]
        if mean_d <= 1e-6:
            return None, None
        if abs(fwd) <= DOLLY_MIN_REL_DIST * mean_d:
            return None, None
        return ("dolly_in" if fwd > 0.0 else "dolly_out"), round(abs(fwd), 2)

    return None, None


def predict(scene: SceneV1, check: CheckV1) -> bool | str | None:
    """Evaluate any CheckV1 under any SceneV1 using geometry.py (shared question bank).

    Pure function of (scene, check): no cap, no priority dropping, never returns `"other"`.
    Returns None only on true abstention or missing/invisible entity.
    """
    pred, _ = _evaluate(scene, check.template, check.entities, check.t, check.t_end)
    return pred


# --------------------------------------------------------------------------- question builders


def _build_question(
    template: Template,
    descs: Sequence[str],
    t: float,
    t_end: float | None = None,
) -> Question:
    if template in ("VISIBLE", "NOT_VISIBLE"):
        d = descs[0]
        return Question(
            type="noul",
            instructions=(
                f"Look at this single film frame. Is {d} visible, even partly, anywhere in the image?"
            ),
            criteria=NoulCriteria(
                true_description=f"{d.capitalize()} is at least partly visible anywhere in the frame.",
                false_description=f"{d.capitalize()} does not appear in the frame.",
            ),
        )

    if template == "SIDE_LEFT":
        d = descs[0]
        return Question(
            type="noul",
            instructions=f"Look at this single film frame. Is {d} in the LEFT half of the image?",
            criteria=NoulCriteria(
                true_description=(
                    f"{d.capitalize()} is visible and its center is left of the vertical midline."
                ),
                false_description=(
                    f"{d.capitalize()} is not visible, or it is in the right half of the image."
                ),
            ),
        )

    if template == "SIDE_RIGHT":
        d = descs[0]
        return Question(
            type="noul",
            instructions=f"Look at this single film frame. Is {d} in the RIGHT half of the image?",
            criteria=NoulCriteria(
                true_description=(
                    f"{d.capitalize()} is visible and its center is right of the vertical midline."
                ),
                false_description=(
                    f"{d.capitalize()} is not visible, or it is in the left half of the image."
                ),
            ),
        )

    if template == "A_LEFT_OF_B":
        da, db = descs[0], descs[1]
        return Question(
            type="noul",
            instructions=(
                f"Look at this single film frame. In this image, is {da} further to the left than {db}?"
            ),
            criteria=NoulCriteria(
                true_description=f"Both are visible and {da} is further left in the image than {db}.",
                false_description=f"Either is not visible, or {da} is further right than {db}.",
            ),
        )

    if template == "CLOSER":
        da, db = descs[0], descs[1]
        return Question(
            type="choice",
            instructions=(
                f"Look at this single film frame. Which of these is closer to the camera in this frame: "
                f"{da} or {db}?"
            ),
            options={
                "a_first": f"{da.capitalize()} is closer to the camera.",
                "b_second": f"{db.capitalize()} is closer to the camera.",
                "other": "One or both are not visible, or it is impossible to tell.",
            },
        )

    if template == "TOWARD":
        d = descs[0]
        t0_s = mmss(t)
        t1_s = mmss(t_end if t_end is not None else t)
        return Question(
            type="noul",
            instructions=(
                f"Image 1 is at {t0_s}, Image 2 is at {t1_s}, same shot. "
                f"Did {d} get closer to the camera between Image 1 and Image 2?"
            ),
            criteria=NoulCriteria(
                true_description=(
                    f"{d.capitalize()} appears larger or nearer to the camera in Image 2 than in Image 1."
                ),
                false_description=f"{d.capitalize()} appears the same distance or further away in Image 2.",
            ),
        )

    if template == "PAN":
        t0_s = mmss(t)
        t1_s = mmss(t_end if t_end is not None else t)
        return Question(
            type="choice",
            instructions=(
                f"Image 1 is at {t0_s}, Image 2 is at {t1_s}, same continuous shot. "
                "How did the camera rotate between them?"
            ),
            options={
                "other": "The camera did not rotate left or right, or it is impossible to tell.",
                "pan_left": "Camera turned to its left (scene slides right).",
                "pan_right": "Camera turned to its right (scene slides left).",
            },
        )

    if template == "DOLLY":
        t0_s = mmss(t)
        t1_s = mmss(t_end if t_end is not None else t)
        return Question(
            type="choice",
            instructions=(
                f"Image 1 is at {t0_s}, Image 2 is at {t1_s}, same continuous shot. "
                "Did the camera physically move toward or away from the scene?"
            ),
            options={
                "dolly_in": "The camera moved forward, toward the scene.",
                "dolly_out": "The camera moved backward, away from the scene.",
                "other": "The camera did not move forward or backward, or it is impossible to tell.",
            },
        )

    raise ValueError(f"Unsupported template: {template}")


def _make_check(
    scene: SceneV1,
    frames_dir: Path,
    template: Template,
    entities: list[str],
    descs: list[str],
    t: float,
    t_end: float | None = None,
    *,
    control: bool = False,
) -> CheckV1 | None:
    pred, margin = _evaluate(scene, template, entities, t, t_end)
    if pred is None:
        return None
    if t_end is None:
        input_ref = InputRef(kind="frame", paths=[_frame_path(frames_dir, t)])
    else:
        input_ref = InputRef(kind="pair", paths=[_frame_path(frames_dir, t), _frame_path(frames_dir, t_end)])
    question = _build_question(template, descs, t, t_end)
    return CheckV1(
        check_id=make_check_id(template, entities, t),
        template=template,
        t=t,
        mmss=mmss(t),
        t_end=t_end,
        input_ref=input_ref,
        entities=list(entities),
        expected=pred,
        margin=margin,
        question=question,
        result=None,
        control=control,
    )


# --------------------------------------------------------------------------- public API


def _default_sample_ts(duration_s: float) -> list[float]:
    if duration_s <= 1.0:
        return [round(duration_s / 2.0, 3)]
    ts: list[float] = []
    t = 0.5
    while t <= duration_s - 0.5 + 1e-9:
        ts.append(round(t, 3))
        t += 1.0
    return ts


def _motion_pairs(scene: SceneV1, sample_ts: Sequence[float]) -> list[tuple[float, float]]:
    duration = scene.clip.duration_s
    pairs: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()

    def _add(t0: float, t1: float) -> None:
        if t1 <= t0 or t0 < 0.0 or t1 > duration + 1e-9:
            return
        k = (round(t0 * 1000), round(t1 * 1000))
        if k not in seen:
            seen.add(k)
            pairs.append((round(t0, 3), round(min(t1, duration), 3)))

    # Motion segments capture slower moves (like dolly over 0..4s) that stay below threshold in 1.5s.
    for seg in scene.camera.motion_segments:
        if seg.t1 - seg.t0 >= 1.0 and (seg.translation != "static" or seg.rotation != "none"):
            _add(seg.t0, seg.t1)

    for t in sample_ts:
        if t + PAIR_DT <= duration + 1e-9:
            _add(t, t + PAIR_DT)

    return pairs


def _interleave_by_template(checks: list[CheckV1]) -> list[CheckV1]:
    """Spread budget fairly across templates (and timestamps) instead of letting one template dominate."""
    buckets: dict[str, list[CheckV1]] = defaultdict(list)
    order: list[str] = []
    for c in checks:
        if c.template not in buckets:
            order.append(c.template)
        buckets[c.template].append(c)
    out: list[CheckV1] = []
    while any(buckets[k] for k in order):
        for k in order:
            if buckets[k]:
                out.append(buckets[k].pop(0))
    return out


def make_checks(
    scene: SceneV1,
    frames_dir: Path | str | None = None,
    sample_ts: list[float] | None = None,
    *,
    max_checks: int = MAX_CHECKS,
) -> list[CheckV1]:
    """Generate 20–60 falsifiable geometry checks for a scene, deduplicated by qkey.

    Priority: motion (PAN/DOLLY/TOWARD) → SIDE (+ 20% mirrored controls) → remaining templates.
    Guarantees `predict(scene, c) == c.expected` for every emitted check.
    """
    fdir = Path(frames_dir) if frames_dir is not None else Path("frames") / scene.clip.id
    ts = list(sample_ts) if sample_ts is not None else _default_sample_ts(scene.clip.duration_s)

    entities: list[Obj | Actor] = [*scene.objects, *scene.actors]
    motion_checks: list[CheckV1] = []
    side_checks: list[CheckV1] = []
    other_checks: list[CheckV1] = []

    # 1. Motion checks (pair frames): PAN, DOLLY, TOWARD
    for t0, t1 in _motion_pairs(scene, ts):
        for tmpl in ("PAN", "DOLLY"):
            c = _make_check(scene, fdir, tmpl, [], [], t0, t1)
            if c is not None:
                motion_checks.append(c)
        for actor in scene.actors:
            c = _make_check(scene, fdir, "TOWARD", [actor.id], [actor.visual_description], t0, t1)
            if c is not None:
                motion_checks.append(c)

    # 2. Single-frame checks: SIDE (+ 20% mirrored controls), VISIBLE/NOT_VISIBLE, A_LEFT_OF_B, CLOSER
    side_count = 0
    for t in ts:
        for ent in entities:
            # VISIBLE vs NOT_VISIBLE
            vis_pred, _ = _evaluate(scene, "VISIBLE", [ent.id], t)
            if vis_pred is True:
                c = _make_check(scene, fdir, "VISIBLE", [ent.id], [ent.visual_description], t)
                if c is not None:
                    other_checks.append(c)
            else:
                nvis_pred, _ = _evaluate(scene, "NOT_VISIBLE", [ent.id], t)
                if nvis_pred is False:
                    c = _make_check(scene, fdir, "NOT_VISIBLE", [ent.id], [ent.visual_description], t)
                    if c is not None:
                        other_checks.append(c)

            # SIDE_LEFT / SIDE_RIGHT (emit the positive side; mirror 20% with control=True)
            sl_pred, _ = _evaluate(scene, "SIDE_LEFT", [ent.id], t)
            if sl_pred is not None:
                primary_tmpl: Template = "SIDE_LEFT" if sl_pred else "SIDE_RIGHT"
                mirror_tmpl: Template = "SIDE_RIGHT" if sl_pred else "SIDE_LEFT"
                c_primary = _make_check(scene, fdir, primary_tmpl, [ent.id], [ent.visual_description], t)
                if c_primary is not None:
                    side_checks.append(c_primary)
                    if side_count % CONTROL_STRIDE == 0:
                        c_mirror = _make_check(
                            scene,
                            fdir,
                            mirror_tmpl,
                            [ent.id],
                            [ent.visual_description],
                            t,
                            control=True,
                        )
                        if c_mirror is not None:
                            side_checks.append(c_mirror)
                    side_count += 1

        # Pairwise entity checks (A_LEFT_OF_B, CLOSER)
        for i, ea in enumerate(entities):
            for eb in entities[i + 1 :]:
                c_ord = _make_check(
                    scene,
                    fdir,
                    "A_LEFT_OF_B",
                    [ea.id, eb.id],
                    [ea.visual_description, eb.visual_description],
                    t,
                )
                if c_ord is not None:
                    other_checks.append(c_ord)

                c_close = _make_check(
                    scene,
                    fdir,
                    "CLOSER",
                    [ea.id, eb.id],
                    [ea.visual_description, eb.visual_description],
                    t,
                )
                if c_close is not None:
                    other_checks.append(c_close)

    # Deduplicate by qkey while respecting priority: motion -> SIDE -> interleaved remaining.
    ordered: list[CheckV1] = [
        *_interleave_by_template(motion_checks),
        *side_checks,
        *_interleave_by_template(other_checks),
    ]

    result: list[CheckV1] = []
    seen_qkeys: set[QKey] = set()
    for c in ordered:
        k = qkey(c)
        if k in seen_qkeys:
            continue
        seen_qkeys.add(k)
        result.append(c)
        if len(result) >= max_checks:
            break

    log.debug(
        "make_checks(%s): emitted %d checks (motion=%d, side=%d, other=%d)",
        scene.clip.id,
        len(result),
        len(motion_checks),
        len(side_checks),
        len(other_checks),
    )
    return result


def frames_needed(checks: Sequence[CheckV1]) -> list[float]:
    """Sorted unique timestamps required to evaluate `checks` (used by S01 frame extraction)."""
    ts: set[float] = set()
    for c in checks:
        ts.add(round(c.t, 3))
        if c.t_end is not None:
            ts.add(round(c.t_end, 3))
    return sorted(ts)


def __getattr__(name: str) -> Any:  # pragma: no cover
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
