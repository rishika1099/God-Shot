"""Scene analyst + director agents for re-shoots (Gemini only).

Spec: S14 (.claude/specs/S14-direct.md) — owner: Akash.

    original clip ──► analyze_scene (Argon, video) ──► SceneBrief: intent, beats, performance, must-preserve
    orig + edited map ──► scene_diff (pure geometry) ──► what changed, in numbers
    brief + diff ──► direct (Argon, HIGH thinking) ──► Direction: how bodies must move now, intent kept
    S12 geometric prompt + Direction ──► directed_prompt ──► Omni
    generated clip ──► preservation (Decisions) ──► are the must-preserve elements still there?

The S12 geometric prompt stays authoritative: agents direct the PERFORMANCE, never the camera or positions.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

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
PATH_SAMPLES = 60
MAX_PRESERVE = 8
PRESERVE_FRAMES = (0.2, 0.5, 0.8)
KEEP_P = 0.5
MOVED_M = 0.3  # an entity moved if its position changed by more than this (room units ≈ m)


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


class Adaptation(_M):
    who: str
    change: str  # what the edit changed for this person, in plain words
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
    preserved: list[str] = Field(default_factory=list)  # how each must-preserve element survives
    conflicts: list[str] = Field(default_factory=list)
    performance_prompt: str


# --------------------------------------------------------------------------- Gemini call (cached)


def _generate_json(
    key_parts: list[bytes | str], contents: Callable[[], list[Any]], schema: type[BaseModel], kind: str
) -> dict:
    """One Argon call with a JSON schema, cached on disk; returns the parsed JSON.

    `contents` is only called on a cache miss, so a cached run never uploads anything.
    """
    cfg = load_config()

    def call() -> dict[str, Any]:
        from google.genai import types

        client = get_client()  # shared client; keep the reference for the whole call
        t0 = time.perf_counter()
        resp = client.models.generate_content(
            model=cfg.MODEL_EXTRACT,
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
    payload = cached([cfg.MODEL_EXTRACT, *key_parts, params], call, kind=kind)
    log.info("%s: %.1fs, tokens %s", kind, payload.get("latency_s", 0), payload.get("usage"))
    return json.loads(payload["text"])


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
    }
    text = json.dumps(data)
    for i, d in names.items():  # belt and braces: ids must never reach a prompt
        text = text.replace(f'"{i}"', json.dumps(d))
    return text


# --------------------------------------------------------------------------- agent 1: scene analyst


def analyze_scene(clip: Path, scene_orig: SceneV1, fps: int = ANALYST_FPS) -> SceneBrief:
    """Argon watches the ORIGINAL clip and writes the scene brief (intent, beats, must-preserve)."""
    from godshot import ingest

    clip = Path(clip)
    rules = (PROMPTS / "scene_brief.md").read_text(encoding="utf-8").strip()
    text = f"{rules}\n\nBLOCKING MAP (overhead, room units ≈ m):\n{_map_summary(scene_orig)}"

    def contents() -> list[Any]:
        from google.genai import types

        video = types.Part(
            file_data=types.FileData(file_uri=ingest.upload(clip), mime_type="video/mp4"),
            video_metadata=types.VideoMetadata(fps=fps),
        )
        return [video, text]

    key = [clip.read_bytes(), text, f"fps={fps}"]
    data = _generate_json(key, contents, SceneBrief, kind="scene_brief")
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
    """What the edit changed, as geometry facts for the director (no judgement, no ids)."""
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
            row |= {
                "status": "changed" if (abs(after["path_m"] - b["path_m"]) > MOVED_M
                                        or math.dist(after["end"], b["end"]) > MOVED_M
                                        or math.dist(after["start"], b["start"]) > MOVED_M) else "unchanged",
                "before": b,
                "speed_ratio": (round(after["speed_mps"] / b["speed_mps"], 2)
                                if b["speed_mps"] > 0.05 else None),
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

    cam_b, cam_a = shot_facts(scene_orig, dur), shot_facts(scene_edited, dur)
    return {
        "duration_s": round(dur, 2),
        "people": people,
        "objects": objects,
        "camera": {
            "before": [f"{s['mmss0']}–{s['mmss1']}: {s['move']}" for s in cam_b["segments"]],
            "after": [f"{s['mmss0']}–{s['mmss1']}: {s['move']}" for s in cam_a["segments"]],
            "start_framing_after": cam_a["start"],
            "end_framing_after": cam_a["end"],
        },
    }


# --------------------------------------------------------------------------- agent 2: director


def direct(brief: SceneBrief, scene_orig: SceneV1, scene_edited: SceneV1,
           duration_s: float | None = None) -> Direction:  # fmt: skip
    """Argon (HIGH thinking) deliberates how behaviour/physics must change for the edit, intent kept."""
    rules = (PROMPTS / "direct.md").read_text(encoding="utf-8").strip()
    diff = scene_diff(scene_orig, scene_edited, duration_s)
    text = (f"{rules}\n\nSCENE BRIEF:\n{brief.model_dump_json(indent=1)}\n\n"
            f"SCENE DIFF (edited vs original, geometry facts):\n{json.dumps(diff, indent=1)}")  # fmt: skip
    return Direction.model_validate(_generate_json([text], lambda: [text], Direction, kind="direction"))


# --------------------------------------------------------------------------- prompt


CONFLICT_RULE = (
    "If anything below conflicts with the camera motion or positions above, the camera and positions win."
)


def directed_prompt(base: str, brief: SceneBrief, direction: Direction) -> str:
    """S12's geometric prompt (verbatim, first) + performance direction + must-keep list."""
    keep = "; ".join(p.element for p in brief.must_preserve)
    wardrobe = "; ".join(f"{p.who}: {p.wardrobe}" for p in brief.performances if p.wardrobe)
    parts = [
        base.rstrip(),
        f"\nSCENE: {brief.logline} {brief.intent}",
        f"PERFORMANCE ({CONFLICT_RULE}) {direction.performance_prompt}",
        f"MUST KEEP: {keep}." if keep else "",
        f"WARDROBE: {wardrobe}." if wardrobe else "",
        f"LIGHT AND ATMOSPHERE: {brief.environment}",
    ]
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------- preservation score


def preservation(video: Path, brief: SceneBrief, work_dir: Path | None = None) -> dict[str, Any]:
    """Ask each must-preserve check (Decisions `noul`) on 3 frames of the video. score = mean P(yes)."""
    from godshot import ingest, verify
    from godshot.reshoot import work_dir_for

    video = Path(video)
    raw = work_dir_for(video) / "raw.mp4"
    if raw.exists():  # labelled reshoot() output: score the unlabelled video
        video = raw
    work = Path(work_dir) if work_dir else video.parent / f"{video.stem}_preservation"
    info = ingest.probe(video, check_length=False)
    last = info.duration_s - 1.2 / info.fps
    frames = [ingest.frame_at(video, min(f * info.duration_s, last), work / "frames")
              for f in PRESERVE_FRAMES]
    items = []
    for p in brief.must_preserve:
        criteria = NoulCriteria(
            true_description=f"Yes: {p.element} is clearly present in at least one of the frames.",
            false_description=f"No: {p.element} is missing, changed or not visible in any frame.",
        )
        q = Question(type="noul", instructions=p.check, criteria=criteria)
        try:
            r = verify.ask(q, frames, context="Three frames, in time order, from one continuous video shot.")
            p_yes = r.p_yes
        except RuntimeError as e:
            log.warning("preservation check failed (%s): %s", p.element, e)
            p_yes = None
        items.append({"element": p.element, "check": p.check, "p_yes": p_yes,
                      "kept": p_yes is not None and p_yes >= KEEP_P})  # fmt: skip
    answered = [i["p_yes"] for i in items if i["p_yes"] is not None]
    result = {
        "score": round(sum(answered) / len(answered), 3) if answered else 0.0,
        "kept": sum(i["kept"] for i in items),
        "n": len(items),
        "frames": [str(f) for f in frames],
        "items": items,
    }
    work.mkdir(parents=True, exist_ok=True)
    (work / "preservation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def combined_score(conformance: float, preserve: float | None, w_preserve: float) -> float:
    """Rank takes on both: follows the edit (conformance) AND keeps the scene (preservation)."""
    if preserve is None:
        return conformance
    return round((1 - w_preserve) * conformance + w_preserve * preserve, 4)


def prepare(clip: Path, scene_orig: SceneV1, scene_edited: SceneV1, out_dir: Path,
            duration_s: float | None = None) -> tuple[SceneBrief, Direction]:  # fmt: skip
    """Run both agents once and save brief.json, diff.json, direction.json to out_dir/direct/."""
    d = Path(out_dir) / "direct"
    d.mkdir(parents=True, exist_ok=True)
    brief = analyze_scene(clip, scene_orig)
    (d / "brief.json").write_text(brief.model_dump_json(indent=2) + "\n", encoding="utf-8")
    diff = scene_diff(scene_orig, scene_edited, duration_s)
    (d / "diff.json").write_text(json.dumps(diff, indent=2) + "\n", encoding="utf-8")
    direction = direct(brief, scene_orig, scene_edited, duration_s)
    (d / "direction.json").write_text(direction.model_dump_json(indent=2) + "\n", encoding="utf-8")
    log.info("direct: %d must-preserve items, %d adaptations, %d conflicts", len(brief.must_preserve),
             len(direction.adaptations), len(direction.conflicts))
    return brief, direction
