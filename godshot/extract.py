"""Argon: clip → scene.v1. Round 0 of the loop == the eval BASELINE.

Spec: S02 (.claude/specs/S02-extract.md) — owner: Akash.

Flow: probe + upload (S01) → one cached Argon call (video part + prompt, JSON schema enforced)
→ sanitize the raw dict (clip metadata, caps, unique ids/descriptions) → validate → (one repair call
on failure) → `postprocess` (pure fixes) → `out/<clip_id>/scene_r0.json` + `loop_log.json` round 0.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from godshot import ingest, ingest_standin
from godshot.cache import cached
from godshot.config import load_config
from godshot.schema import MAX_ACTORS, MAX_OBJECTS, Clip, SceneV1, mmss

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "extract.md"
THINKING_LEVEL = "MEDIUM"
REPAIR_PROMPT = "Fix this JSON to match the schema; change nothing else."
YAW_JUMP_DEG = 90.0
YAW_JUMP_WINDOW_S = 1.1
ENDPOINT_TOL_S = 0.01
CLAMP_MARGIN = 1.0
SAME_SPOT = 0.25  # duplicates closer than this (room units) are the same thing → drop


class ExtractError(RuntimeError):
    """Argon output could not be turned into a valid scene.v1, even after one repair call."""


@dataclass
class CallStats:
    calls: int = 0
    cached_calls: int = 0
    latency_s: float = 0.0
    tokens: dict[str, int] = field(default_factory=dict)

    def add(self, payload: dict[str, Any], was_cached: bool) -> None:
        self.calls += 1
        self.cached_calls += int(was_cached)
        self.latency_s += float(payload.get("latency_s", 0.0))
        for k, v in (payload.get("usage") or {}).items():
            if isinstance(v, int):
                self.tokens[k] = self.tokens.get(k, 0) + v


# --------------------------------------------------------------------------- S01 (with stand-in)


def _probe(clip: Path) -> Clip:
    try:
        return ingest.probe(clip)
    except NotImplementedError:
        return ingest_standin.probe(clip)


def _upload(clip: Path) -> str:
    try:
        return ingest.upload(clip)
    except NotImplementedError:
        return ingest_standin.upload(clip)


# --------------------------------------------------------------------------- API calls (cached)


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def _schema_json() -> str:
    return json.dumps(SceneV1.model_json_schema(by_alias=True), sort_keys=True)


def _usage(resp: Any) -> dict[str, int]:
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return {}
    keys = ("prompt_token_count", "candidates_token_count", "thoughts_token_count", "total_token_count")
    return {k: v for k in keys if isinstance(v := getattr(um, k, None), int)}


def _generate(model: str, contents: list[Any]) -> dict[str, Any]:
    from google.genai import types

    from godshot.config import get_client

    t0 = time.perf_counter()
    resp = get_client().models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=SceneV1.model_json_schema(by_alias=True),
            thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
        ),
    )
    return {"text": resp.text or "", "usage": _usage(resp), "latency_s": time.perf_counter() - t0}


def _cached_call(key_parts: list[bytes | str], fn: Any, kind: str, stats: CallStats) -> dict[str, Any]:
    called = False

    def wrapped() -> dict[str, Any]:
        nonlocal called
        called = True
        return fn()

    payload = cached(key_parts, wrapped, kind=kind)
    stats.add(payload, was_cached=not called)
    return payload


def _call_extract(clip: Path, model: str, prompt: str, fps: int, stats: CallStats) -> str:
    params = json.dumps({"fps": fps, "thinking_level": THINKING_LEVEL, "schema": _schema_json()},
                        sort_keys=True)

    def call() -> dict[str, Any]:
        from google.genai import types

        uri = _upload(clip)  # inside the cache miss path: a cache hit makes zero network calls
        video = types.Part(
            file_data=types.FileData(file_uri=uri, mime_type="video/mp4"),
            video_metadata=types.VideoMetadata(fps=fps),
        )
        return _generate(model, [video, prompt])

    payload = _cached_call([clip.read_bytes(), model, prompt, params], call, "extract", stats)
    return payload["text"]


def _call_repair(model: str, bad_text: str, error: str, stats: CallStats) -> str:
    prompt = f"{REPAIR_PROMPT}\n\nValidation error:\n{error}\n\nJSON:\n{bad_text}"
    params = json.dumps({"thinking_level": THINKING_LEVEL, "schema": _schema_json()}, sort_keys=True)
    payload = _cached_call([model, prompt, params], lambda: _generate(model, [prompt]), "extract_repair",
                           stats)
    return payload["text"]


# --------------------------------------------------------------------------- pre-validation (raw dict)


def _entity_xy(ent: dict[str, Any]) -> tuple[float, float] | None:
    if isinstance(ent.get("center"), list) and len(ent["center"]) == 2:
        return float(ent["center"][0]), float(ent["center"][1])
    track = [k for k in ent.get("track") or [] if isinstance(k, dict)]
    keys = [k for k in track if k.get("visible", True)] or track
    if keys and "x" in keys[0] and "y" in keys[0]:
        return float(keys[0]["x"]), float(keys[0]["y"])
    return None


def _detail(xy: tuple[float, float] | None, ref: tuple[float, float] | None) -> str | None:
    """Distinguishing phrase for a duplicate relative to the first entity; None → same spot, drop it."""
    if xy is None or ref is None:
        return "(another one)"
    dx, dy = xy[0] - ref[0], xy[1] - ref[1]
    if math.hypot(dx, dy) < SAME_SPOT:
        return None
    if abs(dx) >= abs(dy):
        return "(the one further right)" if dx > 0 else "(the one further left)"
    return "(the one farther away)" if dy > 0 else "(the one nearer the camera)"


def dedupe_descriptions(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Make every object/actor visual_description unique (case-insensitive) or drop the duplicate."""
    out = dict(raw)
    notes: list[str] = []
    seen: dict[str, tuple[float, float] | None] = {}
    for group in ("objects", "actors"):
        kept = []
        for ent in raw.get(group) or []:
            ent = dict(ent)
            desc = str(ent.get("visual_description", "")).strip()
            key = desc.lower()
            if key in seen:
                detail = _detail(_entity_xy(ent), seen[key])
                if detail is None:
                    notes.append(f"dropped duplicate {ent.get('id')!r} ({desc!r})")
                    continue
                new, n = f"{desc} {detail}", 2
                while new.lower() in seen:
                    new, n = f"{desc} {detail} #{n}", n + 1
                notes.append(f"renamed duplicate description of {ent.get('id')!r} to {new!r}")
                ent["visual_description"], key = new, new.lower()
            seen[key] = _entity_xy(ent)
            kept.append(ent)
        out[group] = kept
    return out, notes


def _cap(items: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    if len(items) <= n:
        return items
    keep = sorted(range(len(items)), key=lambda i: -float(items[i].get("confidence", 0.5)))[:n]
    return [items[i] for i in sorted(keep)]


def _unique_ids(raw: dict[str, Any]) -> list[str]:
    notes, used = [], set()
    for group in ("objects", "actors"):
        for ent in raw.get(group) or []:
            base = str(ent.get("id") or group[:-1])
            new, n = base, 2
            while new in used:
                new, n = f"{base}_{n}", n + 1
            if new != base:
                notes.append(f"renamed duplicate id {base!r} to {new!r}")
            ent["id"] = new
            used.add(new)
    return notes


def sanitize_raw(raw: Any, clip: Clip) -> dict[str, Any]:
    """Raw model dict → dict that can pass validation: probed clip metadata, caps, unique ids/descs."""
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
    out = dict(raw)
    out["schema"] = "scene.v1"
    out["clip"] = clip.model_dump()  # ffprobe is ground truth, not the model
    notes = []
    for group, cap in (("objects", MAX_OBJECTS), ("actors", MAX_ACTORS)):
        items = [dict(e) for e in out.get(group) or [] if isinstance(e, dict)]
        if len(items) > cap:
            notes.append(f"kept the {cap} most confident of {len(items)} {group}")
        out[group] = _cap(items, cap)
    notes += _unique_ids(out)
    out, dd_notes = dedupe_descriptions(out)
    return _with_notes(out, notes + dd_notes)


def _with_notes(raw: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    if notes:
        prefix = str(raw.get("model_notes") or "").strip()
        raw["model_notes"] = "\n".join([p for p in [prefix] if p] + [f"[postprocess] {n}" for n in notes])
    return raw


# --------------------------------------------------------------------------- post-processing (pure)


def room_bbox(scene: SceneV1) -> tuple[float, float, float, float] | None:
    pts = scene.room.outline
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def clamp_coords(scene: SceneV1, margin: float = CLAMP_MARGIN) -> SceneV1:
    """Clamp object centers and actor/camera keyframes to the room bbox ± margin (no-op without a room)."""
    bb = room_bbox(scene)
    if bb is None:
        return scene
    s = scene.model_copy(deep=True)
    x0, y0, x1, y1 = bb[0] - margin, bb[1] - margin, bb[2] + margin, bb[3] + margin
    for o in s.objects:
        o.center = [_clamp(o.center[0], x0, x1), _clamp(o.center[1], y0, y1)]
    for k in [*s.camera.track, *(k for a in s.actors for k in a.track)]:
        k.x, k.y = _clamp(k.x, x0, x1), _clamp(k.y, y0, y1)
    return s


def _fix_track(track: list[Any], duration: float) -> list[Any]:
    """Clamp t to [0, duration], sort by t, drop same-t duplicates, hold first/last key to 0/duration."""
    keys = []
    for k in sorted(track, key=lambda k: k.t):
        k = k.model_copy()
        k.t = _clamp(k.t, 0.0, duration)
        if keys and abs(k.t - keys[-1].t) < 1e-6:
            continue
        keys.append(k)
    if keys[0].t > ENDPOINT_TOL_S:
        keys.insert(0, keys[0].model_copy(update={"t": 0.0}))
    if keys[-1].t < duration - ENDPOINT_TOL_S:
        keys.append(keys[-1].model_copy(update={"t": duration}))
    return keys


def fix_tracks(scene: SceneV1) -> SceneV1:
    """Sort keyframes by t and insert keyframes at t=0 and t=duration when missing (both tracks)."""
    s = scene.model_copy(deep=True)
    dur = s.clip.duration_s
    s.camera.track = _fix_track(s.camera.track, dur)
    for a in s.actors:
        a.track = _fix_track(a.track, dur)
    for seg in s.camera.motion_segments:
        seg.t0, seg.t1 = _clamp(seg.t0, 0.0, dur), _clamp(seg.t1, 0.0, dur)
    s.camera.motion_segments.sort(key=lambda m: (m.t0, m.t1))
    return s


def _wrap(d: float) -> float:
    """Angle difference into (-180, 180]."""
    d = (d + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def _mid(a: float, b: float) -> float:
    return a + _wrap(b - a) / 2.0


def fix_yaw_jumps(scene: SceneV1) -> SceneV1:
    """Replace yaw jumps > 90° between keys < 1.1 s apart with a midpoint; note each in model_notes.

    A spike (jump out and back) takes the midpoint of its neighbours; otherwise the midpoint of the
    previous and current yaw, which always brings the jump down to ≤ 90°.
    """
    s = scene.model_copy(deep=True)
    track, notes = s.camera.track, []
    for i in range(1, len(track)):
        prev, cur = track[i - 1], track[i]
        if cur.t - prev.t >= YAW_JUMP_WINDOW_S or abs(_wrap(cur.yaw_deg - prev.yaw_deg)) <= YAW_JUMP_DEG:
            continue
        new = _mid(prev.yaw_deg, track[i + 1].yaw_deg) if i + 1 < len(track) else math.nan
        if math.isnan(new) or abs(_wrap(new - prev.yaw_deg)) > YAW_JUMP_DEG:
            new = _mid(prev.yaw_deg, cur.yaw_deg)
        notes.append(f"yaw jump at {mmss(cur.t)}: {cur.yaw_deg:.0f}° → {new:.0f}° (midpoint)")
        cur.yaw_deg = round(new, 3)
    if notes:
        s.model_notes = _with_notes({"model_notes": s.model_notes}, notes)["model_notes"]
    return s


def add_mmss(scene: SceneV1) -> SceneV1:
    """Recompute every keyframe's mmss from t (the model's own mmss is not trusted)."""
    s = scene.model_copy(deep=True)
    for k in [*s.camera.track, *(k for a in s.actors for k in a.track)]:
        k.mmss = mmss(k.t)
    return s


def postprocess(scene: SceneV1) -> SceneV1:
    """Sort keys + endpoints, fix yaw jumps, clamp coords, add mmss. Pure: returns a new scene.

    Description de-duplication runs on the raw dict (`sanitize_raw`) because duplicates fail validation.
    """
    s = fix_tracks(scene)
    s = fix_yaw_jumps(s)
    s = clamp_coords(s)
    s = add_mmss(s)
    return SceneV1.model_validate(s.model_dump(by_alias=True))


def parse_scene(text: str, clip: Clip) -> SceneV1:
    """Model JSON text → validated, post-processed SceneV1. Raises ValueError/ValidationError."""
    return postprocess(SceneV1.model_validate(sanitize_raw(json.loads(text), clip)))


# --------------------------------------------------------------------------- entry points


def extract_with_stats(clip: Path, fps: int | None = None) -> tuple[SceneV1, CallStats]:
    clip = Path(clip)
    cfg = load_config()
    fps = fps or cfg.FPS
    meta = _probe(clip)
    stats = CallStats()
    text = _call_extract(clip, cfg.MODEL_EXTRACT, load_prompt(), fps, stats)
    try:
        scene = parse_scene(text, meta)
    except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
        log.warning("extract output invalid for %s (%s); one repair call", meta.id, type(e).__name__)
        fixed = _call_repair(cfg.MODEL_EXTRACT, text, str(e)[:4000], stats)
        try:
            scene = parse_scene(fixed, meta)
        except (ValueError, ValidationError) as e2:
            raise ExtractError(f"{meta.id}: invalid scene.v1 after repair: {e2}") from e2
    return scene, stats


def extract(clip: Path, fps: int | None = None) -> SceneV1:
    return extract_with_stats(clip, fps)[0]


def loop_log_round0(stats: CallStats, clip: Clip) -> dict[str, Any]:
    """Round-0 entry for loop_log.json (contracts.md fields; verification fields filled by S06)."""
    lat = round(stats.latency_s, 3)
    return {
        "round": 0,
        "pass_rate": None,
        "n_checks": None,
        "n_fail": None,
        "regressions": None,
        "tokens": stats.tokens,
        "latency_s": lat,
        "latency_s_per_10s": round(lat * 10.0 / clip.duration_s, 3),
        "accepted": True,
        "changes": "baseline extraction",
        "calls": stats.calls,
        "cached_calls": stats.cached_calls,
    }


def write_loop_log(path: Path, entry: dict[str, Any]) -> None:
    """Upsert `entry` into the list in loop_log.json by its `round`."""
    rounds: list[dict[str, Any]] = []
    if path.exists():
        rounds = [r for r in json.loads(path.read_text(encoding="utf-8")) if r.get("round") != entry["round"]]
    rounds = sorted([*rounds, entry], key=lambda r: r["round"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rounds, indent=2) + "\n", encoding="utf-8")


def baseline(clip: Path, out_dir: Path, fps: int | None = None) -> SceneV1:
    """Extract and write out_dir/scene_r0.json + loop_log.json round 0."""
    scene, stats = extract_with_stats(clip, fps)
    out_dir = Path(out_dir)
    scene.save(out_dir / "scene_r0.json")
    entry = loop_log_round0(stats, scene.clip)
    write_loop_log(out_dir / "loop_log.json", entry)
    log.info("baseline %s: %.1fs (%.1fs per 10s clip), tokens=%s, cached %d/%d calls", scene.clip.id,
             entry["latency_s"], entry["latency_s_per_10s"], stats.tokens.get("total_token_count"),
             stats.cached_calls, stats.calls)
    return scene
