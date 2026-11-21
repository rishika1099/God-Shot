"""Scene analyst, edit interpreter, director and critic agents for re-shoots (Gemini only).

Spec: S14 (.claude/specs/S14-direct.md) — owner: Akash.

    original clip ──► analyze_scene (Argon, video) ──► SceneBrief: intent, beats, performance, must-preserve
    orig + edited map ──► scene_diff (pure geometry) ──► what changed, in numbers
    brief + diff + free-text request ──► interpret (Hydrogen) ──► ChangeSpec: typed changes, which
                                         must-preserve items are LOCKED vs RELEASED, success checks
    brief + diff + spec ──► direct (Argon, HIGH) ──► Direction: how bodies (and, if asked, the scene) change
    S12 geometric prompt + Pack ──► compose (budgeted) ──► Omni
    take ──► preservation (locked items) + fulfilment (success checks) via Decisions
    take ──► critique (Argon, video) ──► accept | revise + corrections for the next take

The S12 geometric prompt stays first and verbatim: agents direct the PERFORMANCE and requested scene changes,
never the camera path or positions.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from godshot import geometry
from godshot.cache import cached
from godshot.config import get_client, load_config
from godshot.schema import Actor, NoulCriteria, Question, SceneV1

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent / "prompts"
# S02 experiment: MEDIUM reported 0 thinking tokens and missed an orbit that HIGH got right
THINKING_LEVEL = "HIGH"
ANALYST_FPS = 4
CRITIC_FPS = 4
PATH_SAMPLES = 60
MAX_PRESERVE = 8
MAX_SUCCESS = 6
PRESERVE_FRAMES = (0.2, 0.5, 0.8)
KEEP_P = 0.5
MOVED_M = 0.3  # an entity moved if its position changed by more than this (room units ≈ m)
# Omni fails intermittently (generic INVALID_ARGUMENT) on directed prompts of ~4.2k+ chars, while every
# prompt ≤ ~3.2k passed (S14 probes). Lower-priority sections drop first; the retry uses the compact budget.
MAX_PROMPT_CHARS = 3400
COMPACT_PROMPT_CHARS = 2800


# --------------------------------------------------------------------------- agent outputs


class _M(BaseModel):
    model_config = {"extra": "ignore"}


class Beat(_M):
    t0: float
    t1: float
    who: str
    action: str
    detail: str = ""


class Performance(_M):
    who: str
    gait: str
    pace: str
    posture: str
    emotion: str
    wardrobe: str


class PreserveItem(_M):
    element: str
    why: str
    check: str  # yes/no question, answered YES when the element is preserved


class SceneBrief(_M):
    logline: str
    intent: str
    beats: list[Beat]
    performances: list[Performance]
    environment: str
    physics: list[str] = Field(default_factory=list)
    must_preserve: list[PreserveItem] = Field(max_length=MAX_PRESERVE)


ChangeKind = Literal["blocking", "camera", "timing", "performance", "environment", "event", "framing"]


class Change(_M):
    kind: ChangeKind
    what: str
    source: Literal["map", "request"]


class Release(_M):
    index: int  # into SceneBrief.must_preserve
    reason: str


class SuccessCheck(_M):
    element: str
    check: str  # yes/no question, answered YES when the requested change is visible


class ChangeSpec(_M):
    summary: str
    changes: list[Change]
    locked: list[int] = Field(default_factory=list)
    released: list[Release] = Field(default_factory=list)
    success_checks: list[SuccessCheck] = Field(default_factory=list, max_length=MAX_SUCCESS)
    assumptions: list[str] = Field(default_factory=list)


class Adaptation(_M):
    who: str
    change: str  # what the edit changed for this person/thing, in plain words
    reasoning: str  # the physical / dramatic reasoning
    behavior: str  # the adjusted behaviour


class Retime(_M):
    beat: str
    t0: float
    t1: float


class Direction(_M):
    change_summary: str
    adaptations: list[Adaptation]
    retiming: list[Retime] = Field(default_factory=list)
    preserved: list[str] = Field(default_factory=list)  # how each LOCKED element survives
    conflicts: list[str] = Field(default_factory=list)
    performance_prompt: str
    scene_changes_prompt: str = ""  # requested non-body changes (light, weather, props, events)


IssueKind = Literal[
    "identity", "wardrobe", "action", "physics", "blocking", "camera", "environment", "request", "artifact"
]


class Issue(_M):
    kind: IssueKind
    at: str  # MM:SS–MM:SS or "throughout"
    problem: str
    fix: str


class Critique(_M):
    verdict: Literal["accept", "revise"]
    issues: list[Issue] = Field(default_factory=list)
    corrections: str = ""  # 1–4 sentences for the next take; empty when accepted


@dataclass
class Pack:
    """Everything the prompt composer needs for one take."""

    brief: SceneBrief
    direction: Direction
    spec: ChangeSpec | None = None
    request: str = ""
    corrections: list[str] = field(default_factory=list)  # critic rounds, oldest first
    budget: int | None = None  # prompt length cap; None = MAX_PROMPT_CHARS


# --------------------------------------------------------------------------- Gemini call (cached)


def _generate_json(
    key_parts: list[bytes | str],
    contents: Callable[[], list[Any]],
    schema: type[BaseModel],
    kind: str,
    model: str | None = None,
) -> dict:
    """One Gemini call with a JSON schema, cached on disk; returns the parsed JSON.

    `contents` is only called on a cache miss, so a cached run never uploads anything.
    """
    cfg = load_config()
    model = model or cfg.MODEL_EXTRACT

    def call() -> dict[str, Any]:
        from google.genai import types

        client = get_client()  # shared client; keep the reference for the whole call
        t0 = time.perf_counter()
        resp = client.models.generate_content(
            model=model,
            contents=contents(),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
                thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
            ),
        )
        um = getattr(resp, "usage_metadata", None)
        usage = {k: v for k in ("prompt_token_count", "candidates_token_count", "thoughts_token_count")
                 if isinstance(v := getattr(um, k, None), int)}  # fmt: skip
        return {"text": resp.text or "", "usage": usage, "latency_s": round(time.perf_counter() - t0, 2)}

    params = json.dumps({"thinking": THINKING_LEVEL, "schema": schema.model_json_schema()}, sort_keys=True)
    payload = cached([model, *key_parts, params], call, kind=kind)
    log.info("%s: %.1fs, tokens %s", kind, payload.get("latency_s", 0), payload.get("usage"))
    return json.loads(payload["text"])


def _video_part(video: Path, fps: int) -> Any:
    from google.genai import types

    from godshot import ingest

    return types.Part(
        file_data=types.FileData(file_uri=ingest.upload(video), mime_type="video/mp4"),
        video_metadata=types.VideoMetadata(fps=fps),
    )


def _rules(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def _map_summary(scene: SceneV1) -> str:
    """Compact JSON of who/what is where for the agents (descriptions, not ids)."""
    names = {e.id: e.visual_description for e in [*scene.actors, *scene.objects]}
    data = {
        "duration_s": scene.clip.duration_s,
        "people": [{"who": a.visual_description, "track": [[k.t, round(k.x, 2), round(k.y, 2), k.visible]
                                                           for k in a.track]} for a in scene.actors],
        "objects": [{"what": o.visual_description, "at": [round(v, 2) for v in o.center]}
                    for o in scene.objects],
        "camera": [[k.t, round(k.x, 2), round(k.y, 2), round(k.yaw_deg)] for k in scene.camera.track],
    }  # fmt: skip
    text = json.dumps(data)
    for i, d in names.items():  # belt and braces: ids must never reach a prompt
        text = text.replace(f'"{i}"', json.dumps(d))
    return text


# --------------------------------------------------------------------------- agent 1: scene analyst


def analyze_scene(clip: Path, scene_orig: SceneV1, fps: int = ANALYST_FPS) -> SceneBrief:
    """Argon watches the ORIGINAL clip and writes the scene brief (intent, beats, must-preserve)."""
    clip = Path(clip)
    map_text = _map_summary(scene_orig)
    text = f"{_rules('scene_brief.md')}\n\nBLOCKING MAP (overhead, room units ≈ m):\n{map_text}"
    key = [clip.read_bytes(), text, f"fps={fps}"]
    data = _generate_json(key, lambda: [_video_part(clip, fps), text], SceneBrief, kind="scene_brief")
    data["must_preserve"] = data.get("must_preserve", [])[:MAX_PRESERVE]
    return SceneBrief.model_validate(data)


# --------------------------------------------------------------------------- scene diff (pure)


def gait(speed_mps: float) -> str:
    if speed_mps < 0.3:
        return "standing / shifting weight"
    if speed_mps <= 1.6:
        return "walk"
    if speed_mps <= 3.0:
        return "jog"
    return "run"


def actor_motion(actor: Actor, dur: float, n: int = PATH_SAMPLES) -> dict[str, Any]:
    """Path length, visible time, mean speed, speed while moving and gait over [0, dur] (units ≈ m)."""
    ts = [dur * i / n for i in range(n + 1)]
    pts = [geometry.interp_actor(actor, t) for t in ts]
    steps = [math.dist(a[:2], b[:2]) for a, b in pairwise(pts) if a[3] and b[3]]
    length, visible, dt = sum(steps), len(steps) * dur / n, dur / n
    speed = length / visible if visible else 0.0
    moving = [d for d in steps if d / dt >= 0.3]  # ignore standing still when judging the gait
    moving_speed = sum(moving) / (len(moving) * dt) if moving else 0.0
    return {
        "path_m": round(length, 2),
        "visible_s": round(visible, 2),
        "speed_mps": round(speed, 2),
        "moving_s": round(len(moving) * dt, 2),
        "moving_speed_mps": round(moving_speed, 2),
        "gait": gait(moving_speed),
        "start": [round(v, 2) for v in pts[0][:2]],
        "end": [round(v, 2) for v in pts[-1][:2]],
    }


def _mean_cam_dist(scene: SceneV1, actor: Actor, dur: float, n: int = 20) -> float:
    ds = []
    for i in range(n + 1):
        t = dur * i / n
        x, y, _, vis = geometry.interp_actor(actor, t)
        if vis:
            ds.append(geometry.distance(geometry.interp_camera(scene, t), (x, y)))
    return round(sum(ds) / len(ds), 2) if ds else 0.0


def _nearest_object(scene: SceneV1, xy: list[float]) -> str | None:
    if not scene.objects:
        return None
    o = min(scene.objects, key=lambda o: math.dist(o.center, xy))
    return f"{o.visual_description} ({math.dist(o.center, xy):.1f} m away)"


def scene_diff(scene_orig: SceneV1, scene_edited: SceneV1, duration_s: float | None = None) -> dict[str, Any]:
    """What the map edit changed, as geometry facts for the agents (no judgement, no ids)."""
    from godshot.reshoot import shot_facts  # S12

    dur = min(duration_s or scene_edited.clip.duration_s, scene_edited.clip.duration_s)
    orig_actors = {a.id: a for a in scene_orig.actors}
    people = []
    for a in scene_edited.actors:
        before = orig_actors.get(a.id)
        after = actor_motion(a, dur)
        row: dict[str, Any] = {"who": a.visual_description, "after": after}
        if before is None:
            row["status"] = "added"
        else:
            b = actor_motion(before, dur)
            changed = (
                abs(after["path_m"] - b["path_m"]) > MOVED_M
                or math.dist(after["end"], b["end"]) > MOVED_M
                or math.dist(after["start"], b["start"]) > MOVED_M
            )
            row |= {
                "status": "changed" if changed else "unchanged",
                "before": b,
                "speed_ratio": (round(after["moving_speed_mps"] / b["moving_speed_mps"], 2)
                                if b["moving_speed_mps"] > 0.05 else None),
                "end_shift_m": round(math.dist(after["end"], b["end"]), 2),
                "camera_distance_m": {"before": _mean_cam_dist(scene_orig, before, dur),
                                      "after": _mean_cam_dist(scene_edited, a, dur)},
                "ends_near": {"before": _nearest_object(scene_orig, b["end"]),
                              "after": _nearest_object(scene_edited, after["end"])},
            }  # fmt: skip
        people.append(row)
    edited_ids = {a.id for a in scene_edited.actors}
    people += [{"who": a.visual_description, "status": "removed"} for a in scene_orig.actors
               if a.id not in edited_ids]  # fmt: skip

    orig_objs = {o.id: o for o in scene_orig.objects}
    objects = []
    for o in scene_edited.objects:
        b = orig_objs.get(o.id)
        if b is None:
            objects.append({"what": o.visual_description, "status": "added", "at": o.center})
        elif math.dist(o.center, b.center) > MOVED_M or abs(o.rotation_deg - b.rotation_deg) > 10:
            objects.append({"what": o.visual_description, "status": "moved", "from": b.center, "to": o.center,
                            "moved_m": round(math.dist(o.center, b.center), 2)})  # fmt: skip
    edited_objs = {o.id for o in scene_edited.objects}
    objects += [{"what": o.visual_description, "status": "removed"} for o in scene_orig.objects
                if o.id not in edited_objs]  # fmt: skip

    cam_b, cam_a = shot_facts(scene_orig, dur), shot_facts(scene_edited, dur)
    before = [f"{s['mmss0']}–{s['mmss1']}: {s['move']}" for s in cam_b["segments"]]
    after = [f"{s['mmss0']}–{s['mmss1']}: {s['move']}" for s in cam_a["segments"]]
    return {
        "duration_s": round(dur, 2),
        "people": people,
        "objects": objects,
        "camera": {"changed": before != after or cam_b["lens"] != cam_a["lens"], "before": before,
                   "after": after, "lens_after": cam_a["lens"], "start_framing_after": cam_a["start"],
                   "end_framing_after": cam_a["end"]},
    }  # fmt: skip


# --------------------------------------------------------------------------- agent 2: edit interpreter


def normalize_spec(spec: ChangeSpec, n_items: int) -> ChangeSpec:
    """Every must-preserve index is exactly one of locked/released; released wins; out-of-range dropped."""
    released = {r.index: r for r in spec.released if 0 <= r.index < n_items}
    locked = [i for i in range(n_items) if i not in released]
    update = {
        "released": sorted(released.values(), key=lambda r: r.index),
        "locked": locked,
        "success_checks": spec.success_checks[:MAX_SUCCESS],
    }
    return spec.model_copy(update=update)


def interpret(brief: SceneBrief, diff: dict[str, Any], request: str = "") -> ChangeSpec:
    """Hydrogen turns the map diff + optional free-text request into a typed change spec with lock/release."""
    items = "\n".join(f"[{i}] {p.element}" for i, p in enumerate(brief.must_preserve))
    text = "\n\n".join([
        _rules("interpret.md"),
        f"USER REQUEST (free text; may be empty):\n{request.strip() or '(none)'}",
        f"MAP DIFF (edited vs original, geometry facts):\n{json.dumps(diff, indent=1)}",
        f"SCENE BRIEF:\n{brief.model_dump_json(indent=1)}",
        f"MUST-PRESERVE ITEMS (by index):\n{items}",
    ])
    model = load_config().MODEL_CHEAP
    data = _generate_json([text], lambda: [text], ChangeSpec, kind="change_spec", model=model)
    return normalize_spec(ChangeSpec.model_validate(data), len(brief.must_preserve))


def locked_items(brief: SceneBrief, spec: ChangeSpec | None) -> list[PreserveItem]:
    if spec is None:
        return list(brief.must_preserve)
    return [brief.must_preserve[i] for i in spec.locked if 0 <= i < len(brief.must_preserve)]


# --------------------------------------------------------------------------- agent 3: director


def direct(brief: SceneBrief, scene_orig: SceneV1, scene_edited: SceneV1, duration_s: float | None = None,
           spec: ChangeSpec | None = None, request: str = "") -> Direction:  # fmt: skip
    """Argon (HIGH) deliberates how behaviour/physics (and requested scene changes) must play, intent kept."""
    diff = scene_diff(scene_orig, scene_edited, duration_s)
    text = (f"{_rules('direct.md')}\n\nSCENE BRIEF:\n{brief.model_dump_json(indent=1)}\n\n"
            f"SCENE DIFF (edited vs original, geometry facts):\n{json.dumps(diff, indent=1)}")  # fmt: skip
    if spec is not None:
        locked = "\n".join(f"- {p.element}" for p in locked_items(brief, spec)) or "- (none)"
        released = "\n".join(f"- {brief.must_preserve[r.index].element}: {r.reason}" for r in spec.released)
        text += "\n\n" + "\n\n".join([
            f"USER REQUEST:\n{request.strip() or '(none: map edit only)'}",
            f"CHANGE SPEC:\n{spec.model_dump_json(indent=1)}",
            f"LOCKED (must survive):\n{locked}",
            f"RELEASED (the user asked to change these; do NOT preserve them):\n{released or '- (none)'}",
        ])
    return Direction.model_validate(_generate_json([text], lambda: [text], Direction, kind="direction"))


# --------------------------------------------------------------------------- prompt


CONFLICT_RULE = (
    "If anything below conflicts with the camera motion or positions above, the camera and positions win."
)


def _released_kinds(spec: ChangeSpec | None) -> set[str]:
    return {c.kind for c in spec.changes if c.source == "request"} if spec else set()


def compose(base: str, pack: Pack | tuple[SceneBrief, Direction]) -> str:
    """S12 geometric prompt (verbatim, first) + direction sections, within MAX_PROMPT_CHARS.

    Sections in priority order; the lowest-priority ones are dropped first if the prompt is too long. The
    base is never cut.
    """
    if isinstance(pack, tuple):
        pack = Pack(brief=pack[0], direction=pack[1])
    brief, d, spec = pack.brief, pack.direction, pack.spec
    requested = _released_kinds(spec)
    keep = "; ".join(p.element for p in locked_items(brief, spec))
    wardrobe = "; ".join(f"{p.who}: {p.wardrobe}" for p in brief.performances if p.wardrobe)
    sections = [  # (priority: lower = more important, text)
        (1, f"PERFORMANCE ({CONFLICT_RULE}) {d.performance_prompt}"),
        (2, f"REQUESTED CHANGES: {d.scene_changes_prompt}" if d.scene_changes_prompt else ""),
        (3, "CORRECTIONS FROM THE PREVIOUS TAKE: " + " ".join(pack.corrections) if pack.corrections else ""),
        (4, f"MUST KEEP: {keep}." if keep else ""),
        (5, f"WARDROBE: {wardrobe}." if wardrobe and "performance" not in requested else ""),
        (6, f"LIGHT AND ATMOSPHERE: {brief.environment}" if "environment" not in requested else ""),
        (7, f"SCENE: {brief.logline} {brief.intent}"),
    ]
    budget = pack.budget or MAX_PROMPT_CHARS
    kept = [(p, s) for p, s in sections if s]
    prompt = ""
    while kept:
        prompt = "\n".join([base.rstrip(), *(s for _, s in sorted(kept))])
        if len(prompt) <= budget:
            break
        kept.remove(max(kept))  # drop the least important section
    else:
        prompt = base.rstrip()
    return prompt


def directed_prompt(base: str, brief: SceneBrief, direction: Direction) -> str:
    """Back-compatible wrapper (brief + direction, no spec)."""
    return compose(base, Pack(brief=brief, direction=direction))


# --------------------------------------------------------------------------- scoring (Decisions)


def _frames(video: Path, work: Path) -> tuple[Path, list[Path]]:
    from godshot import ingest
    from godshot.reshoot import work_dir_for

    video = Path(video)
    raw = work_dir_for(video) / "raw.mp4"
    if raw.exists():  # labelled reshoot() output: score the unlabelled video
        video = raw
    info = ingest.probe(video, check_length=False)
    last = info.duration_s - 1.2 / info.fps
    return video, [ingest.frame_at(video, min(f * info.duration_s, last), work / "frames")
                   for f in PRESERVE_FRAMES]  # fmt: skip


def _ask_items(video: Path, items: Sequence[tuple[str, str]], work: Path, name: str) -> dict[str, Any]:
    """Ask each (element, yes/no check) on 3 frames; score = mean P(yes); kept when P(yes) ≥ 0.5."""
    from godshot import verify

    work = Path(work)
    video, frames = _frames(video, work)
    rows = []
    for element, check in items:
        criteria = NoulCriteria(
            true_description=f"Yes: {element} is clearly visible in at least one of the frames.",
            false_description=f"No: {element} is missing, changed or not visible in any frame.",
        )
        try:
            q = Question(type="noul", instructions=check, criteria=criteria)
            r = verify.ask(q, frames, context="Three frames, in time order, from one continuous video shot.")
            p_yes = r.p_yes
        except RuntimeError as e:
            log.warning("%s check failed (%s): %s", name, element, e)
            p_yes = None
        rows.append({"element": element, "check": check, "p_yes": p_yes,
                     "kept": p_yes is not None and p_yes >= KEEP_P})  # fmt: skip
    answered = [r["p_yes"] for r in rows if r["p_yes"] is not None]
    result = {
        "score": round(sum(answered) / len(answered), 3) if answered else None,
        "kept": sum(r["kept"] for r in rows),
        "n": len(rows),
        "source": str(video),
        "items": rows,
    }
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def preservation(video: Path, brief: SceneBrief, work_dir: Path | None = None,
                 spec: ChangeSpec | None = None) -> dict[str, Any]:  # fmt: skip
    """Are the LOCKED must-preserve elements still there? (All items when there is no spec.)"""
    video = Path(video)
    work = Path(work_dir) if work_dir else video.parent / f"{video.stem}_preservation"
    r = _ask_items(video, [(p.element, p.check) for p in locked_items(brief, spec)], work, "preservation")
    r["score"] = r["score"] if r["score"] is not None else 0.0
    return r


def fulfilment(video: Path, spec: ChangeSpec, work_dir: Path | None = None) -> dict[str, Any] | None:
    """Did the requested changes happen? None when the spec has no success checks."""
    if not spec.success_checks:
        return None
    video = Path(video)
    work = Path(work_dir) if work_dir else video.parent / f"{video.stem}_fulfilment"
    return _ask_items(video, [(c.element, c.check) for c in spec.success_checks], work, "fulfilment")


def combined_score(conformance: float, preserve: float | None, w_preserve: float,
                   fulfil: float | None = None) -> float:  # fmt: skip
    """Rank takes on: follows the map (conformance) AND keeps the scene / does what was asked."""
    scene = [s for s in (preserve, fulfil) if s is not None]
    if not scene:
        return conformance
    return round((1 - w_preserve) * conformance + w_preserve * sum(scene) / len(scene), 4)


# --------------------------------------------------------------------------- agent 4: critic


def critique(video: Path, pack: Pack, scores: dict[str, Any]) -> Critique:
    """Argon watches a generated take and names what to fix (identity, action, physics, request …)."""
    from godshot.reshoot import work_dir_for

    video = Path(video)
    raw = work_dir_for(video) / "raw.mp4"
    video = raw if raw.exists() else video
    spec = pack.spec
    locked = [p.element for p in locked_items(pack.brief, spec)]
    text = "\n\n".join([
        _rules("critic.md"),
        f"SCENE BRIEF (the original shot):\n{pack.brief.model_dump_json(indent=1)}",
        f"USER REQUEST:\n{pack.request.strip() or '(none: map edit only)'}",
        f"CHANGE SPEC:\n{spec.model_dump_json(indent=1) if spec else '(none)'}",
        "LOCKED ELEMENTS (must survive):\n" + "\n".join(f"- {e}" for e in locked),
        f"DIRECTION GIVEN FOR THIS TAKE:\n{pack.direction.performance_prompt}\n"
        f"{pack.direction.scene_changes_prompt}",
        f"AUTOMATIC SCORES (frame checks; trust them less than what you see):\n"
        f"{json.dumps(scores, indent=1, default=str)}",
    ])
    key = [video.read_bytes(), text, f"fps={CRITIC_FPS}"]
    return Critique.model_validate(
        _generate_json(key, lambda: [_video_part(video, CRITIC_FPS), text], Critique, kind="critique")
    )


def score_summary(conf: dict[str, Any], pres: dict[str, Any] | None, ful: dict[str, Any] | None) -> dict:
    """Compact scores for the critic: failing map checks by template + items lost / not done."""
    misses: dict[str, int] = {}
    for c in conf.get("checks", []):
        if c.get("usable") and not c.get("hit"):
            misses[c["template"]] = misses.get(c["template"], 0) + 1
    return {
        "map_conformance": conf.get("score"),
        "map_misses_by_template": misses,
        "locked_elements_lost": [i["element"] for i in (pres or {}).get("items", []) if not i["kept"]],
        "requested_changes_missing": [i["element"] for i in (ful or {}).get("items", []) if not i["kept"]],
    }


# --------------------------------------------------------------------------- orchestration


def prepare(clip: Path, scene_orig: SceneV1, scene_edited: SceneV1, out_dir: Path,
            duration_s: float | None = None, request: str = "") -> Pack:  # fmt: skip
    """Analyst → diff → interpreter → director, once per edit; artifacts in out_dir/direct/."""
    d = Path(out_dir) / "direct"
    d.mkdir(parents=True, exist_ok=True)
    brief = analyze_scene(clip, scene_orig)
    (d / "brief.json").write_text(brief.model_dump_json(indent=2) + "\n", encoding="utf-8")
    diff = scene_diff(scene_orig, scene_edited, duration_s)
    (d / "diff.json").write_text(json.dumps(diff, indent=2) + "\n", encoding="utf-8")
    spec = interpret(brief, diff, request)
    (d / "change_spec.json").write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    direction = direct(brief, scene_orig, scene_edited, duration_s, spec=spec, request=request)
    (d / "direction.json").write_text(direction.model_dump_json(indent=2) + "\n", encoding="utf-8")
    log.info("direct: %d locked / %d released, %d success checks, %d adaptations, %d conflicts",
             len(spec.locked), len(spec.released), len(spec.success_checks), len(direction.adaptations),
             len(direction.conflicts))  # fmt: skip
    return Pack(brief=brief, direction=direction, spec=spec, request=request)
