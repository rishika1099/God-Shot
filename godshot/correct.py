"""Argon (HIGH thinking): failed checks + frames → corrected scene.v1.

Spec: S06 (.claude/specs/S06*.md) — owner: Rishika.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from godshot.cache import cached
from godshot.config import get_client, load_config
from godshot.schema import (
    MAX_ACTORS,
    MAX_OBJECTS,
    Actor,
    CheckV1,
    LowConf,
    Obj,
    SceneV1,
    mmss,
)

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "correct.md"
_MERGE_GAP_S = 1.5
MAX_FAILING_FRAMES = 6
_RETRY_BACKOFFS_S = (4.0, 10.0)


# --------------------------------------------------------------------------- identity freezing


def _mean_actor_pos(actor: Actor) -> tuple[float, float]:
    xs = [k.x for k in actor.track]
    ys = [k.y for k in actor.track]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _match_objects(cand_objs: Sequence[Obj], prev_objs: Sequence[Obj]) -> list[Obj]:
    """Restore id + visual_description from prev objects: id -> description -> type+nearest."""
    available: dict[str, Obj] = {o.id: o for o in prev_objs}
    assignments: list[Obj | None] = [None] * len(cand_objs)

    # Pass 1: exact id match
    for idx, co in enumerate(cand_objs):
        if co.id in available:
            assignments[idx] = available.pop(co.id)

    # Pass 2: exact visual_description match (case-insensitive)
    for idx, co in enumerate(cand_objs):
        if assignments[idx] is not None:
            continue
        key = co.visual_description.strip().lower()
        for pid, po in list(available.items()):
            if po.visual_description.strip().lower() == key:
                assignments[idx] = available.pop(pid)
                break

    # Pass 3: same type + nearest center position
    for idx, co in enumerate(cand_objs):
        if assignments[idx] is not None:
            continue
        same_type = [po for po in available.values() if po.type.strip().lower() == co.type.strip().lower()]
        if not same_type:
            continue
        nearest = min(
            same_type,
            key=lambda po: math.hypot(po.center[0] - co.center[0], po.center[1] - co.center[1]),
        )
        assignments[idx] = available.pop(nearest.id)

    used_ids: set[str] = set()
    used_descs: set[str] = set()
    out: list[Obj] = []
    for co, matched in zip(cand_objs, assignments, strict=True):
        if matched is not None:
            fixed = co.model_copy(
                update={"id": matched.id, "visual_description": matched.visual_description}
            )
        else:
            # New object: ensure unique id/description so SceneV1 validation cannot fail.
            new_id = co.id
            suffix = 1
            while new_id in used_ids or any(p.id == new_id for p in prev_objs):
                suffix += 1
                new_id = f"{co.id}_{suffix}"
            new_desc = co.visual_description
            while new_desc.strip().lower() in used_descs:
                new_desc = f"{new_desc} (extra)"
            fixed = co.model_copy(update={"id": new_id, "visual_description": new_desc})
        used_ids.add(fixed.id)
        used_descs.add(fixed.visual_description.strip().lower())
        out.append(fixed)
        if len(out) >= MAX_OBJECTS:
            break
    return out


def _match_actors(cand_actors: Sequence[Actor], prev_actors: Sequence[Actor]) -> list[Actor]:
    """Restore id + visual_description from prev actors: id -> description -> nearest track."""
    available: dict[str, Actor] = {a.id: a for a in prev_actors}
    assignments: list[Actor | None] = [None] * len(cand_actors)

    # Pass 1: exact id match
    for idx, ca in enumerate(cand_actors):
        if ca.id in available:
            assignments[idx] = available.pop(ca.id)

    # Pass 2: exact visual_description match
    for idx, ca in enumerate(cand_actors):
        if assignments[idx] is not None:
            continue
        key = ca.visual_description.strip().lower()
        for pid, pa in list(available.items()):
            if pa.visual_description.strip().lower() == key:
                assignments[idx] = available.pop(pid)
                break

    # Pass 3: nearest mean track position
    for idx, ca in enumerate(cand_actors):
        if assignments[idx] is not None or not available:
            continue
        cx, cy = _mean_actor_pos(ca)
        nearest = min(
            available.values(),
            key=lambda pa: math.hypot(_mean_actor_pos(pa)[0] - cx, _mean_actor_pos(pa)[1] - cy),
        )
        assignments[idx] = available.pop(nearest.id)

    used_ids: set[str] = set()
    used_descs: set[str] = set()
    out: list[Actor] = []
    for ca, matched in zip(cand_actors, assignments, strict=True):
        if matched is not None:
            fixed = ca.model_copy(
                update={"id": matched.id, "visual_description": matched.visual_description}
            )
        else:
            new_id = ca.id
            suffix = 1
            while new_id in used_ids or any(p.id == new_id for p in prev_actors):
                suffix += 1
                new_id = f"{ca.id}_{suffix}"
            new_desc = ca.visual_description
            while new_desc.strip().lower() in used_descs:
                new_desc = f"{new_desc} (extra)"
            fixed = ca.model_copy(update={"id": new_id, "visual_description": new_desc})
        used_ids.add(fixed.id)
        used_descs.add(fixed.visual_description.strip().lower())
        out.append(fixed)
        if len(out) >= MAX_ACTORS:
            break
    return out


def freeze_identity(cand: SceneV1, prev: SceneV1) -> SceneV1:
    """Restore entity ids and visual_descriptions from `prev` without un-deleting missing entities."""
    fixed_objects = _match_objects(cand.objects, prev.objects)
    fixed_actors = _match_actors(cand.actors, prev.actors)
    # Guard against cross-list id/description collisions (objects vs actors)
    obj_ids = {o.id for o in fixed_objects}
    obj_descs = {o.visual_description.strip().lower() for o in fixed_objects}
    clean_actors: list[Actor] = []
    for a in fixed_actors:
        aid = a.id
        desc = a.visual_description
        while aid in obj_ids:
            aid = f"{aid}_actor"
        while desc.strip().lower() in obj_descs:
            desc = f"{desc} (person)"
        clean_actors.append(a.model_copy(update={"id": aid, "visual_description": desc}))

    return SceneV1(
        clip=prev.clip,
        room=cand.room,
        objects=fixed_objects,
        actors=clean_actors,
        camera=cand.camera,
        low_confidence=list(cand.low_confidence),
        model_notes=cand.model_notes,
    )


# --------------------------------------------------------------------------- low-confidence tagging


def mark_low_confidence(scene: SceneV1, still_failing: Sequence[CheckV1]) -> SceneV1:
    """Group remaining failing checks by entity and overlapping/adjacent time windows."""
    by_entity: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for c in still_failing:
        t0 = c.t
        t1 = c.t_end if c.t_end is not None else c.t
        targets = list(c.entities) if c.entities else ["camera"]
        for eid in targets:
            by_entity[eid].append((t0, t1, c.check_id))

    merged: list[LowConf] = []
    for eid in sorted(by_entity):
        spans = sorted(by_entity[eid], key=lambda item: (item[0], item[1]))
        cur_t0, cur_t1, first_cid = spans[0]
        cids: list[str] = [first_cid]
        for t0, t1, cid in spans[1:]:
            if t0 <= cur_t1 + _MERGE_GAP_S:
                cur_t1 = max(cur_t1, t1)
                if cid not in cids:
                    cids.append(cid)
            else:
                merged.append(LowConf(entity_id=eid, t0=cur_t0, t1=cur_t1, failed_checks=cids))
                cur_t0, cur_t1 = t0, t1
                cids = [cid]
        merged.append(LowConf(entity_id=eid, t0=cur_t0, t1=cur_t1, failed_checks=cids))

    return scene.model_copy(update={"low_confidence": merged})


# --------------------------------------------------------------------------- prompt + Argon call


def _format_observed(check: CheckV1) -> tuple[str, str]:
    res = check.result
    if res is None:
        return "unknown", "-"
    if check.question.type == "noul" and res.p_yes is not None:
        obs = "true" if res.p_yes >= 0.5 else "false"
        return obs, f"{res.p_yes:.2f}"
    if res.choice_probs:
        top_k, top_p = max(res.choice_probs.items(), key=lambda kv: kv[1])
        return top_k, f"{top_p:.2f}"
    return "unknown", f"{res.confidence:.2f}"


def _collect_failing_frames(
    failed: Sequence[CheckV1],
    frames_dir: Path | None,
    max_frames: int = MAX_FAILING_FRAMES,
) -> tuple[list[tuple[str, Path]], list[str]]:
    """Return deduplicated `(label, path)` list for top-confidence failing frames (capped for latency)."""
    # Prefer highest-confidence failures (and motion/side checks) so Argon stays under latency budget.
    ranked = sorted(
        failed,
        key=lambda c: (c.result.confidence if c.result is not None else 0.5),
        reverse=True,
    )
    seen: set[str] = set()
    entries: list[tuple[str, Path]] = []
    labels: list[str] = []
    for c in ranked:
        ts = [c.t] if c.t_end is None else [c.t, c.t_end]
        for path_str, t_val in zip(c.input_ref.paths, ts, strict=False):
            if path_str in seen:
                continue
            if len(entries) >= max_frames:
                return entries, labels
            seen.add(path_str)
            p = Path(path_str)
            if not p.exists() and frames_dir is not None:
                candidate = frames_dir / p.name
                if candidate.exists():
                    p = candidate
            idx = len(entries) + 1
            label = f"Image {idx} is at {mmss(t_val)} ({p.name})"
            labels.append(label)
            entries.append((label, p))
    return entries, labels


def _is_transient_error(err: Exception) -> bool:
    code = getattr(err, "code", None) or getattr(err, "status_code", None)
    if isinstance(code, int) and (code == 429 or 500 <= code < 600):
        return True
    msg = str(err).upper()
    return any(tok in msg for tok in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE"))


def _generate_with_retry(client: Any, *, model: str, contents: Any, config: Any) -> Any:
    for attempt in range(len(_RETRY_BACKOFFS_S) + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as err:
            if attempt >= len(_RETRY_BACKOFFS_S) or not _is_transient_error(err):
                raise
            wait = _RETRY_BACKOFFS_S[attempt]
            log.warning("Argon transient error (%s); retrying in %.0fs", err, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _compact_passed_line(c: CheckV1) -> str:
    t_label = c.mmss if c.t_end is None else f"{c.mmss}–{mmss(c.t_end)}"
    if c.template == "CLOSER" and len(c.entities) == 2:
        closer, farther = (
            (c.entities[0], c.entities[1]) if c.expected == "a_first" else (c.entities[1], c.entities[0])
        )
        return (
            f"- [{c.check_id}] CLOSER @ {t_label}: KEEP {closer} closer than {farther} (ratio > 1.3)"
        )
    if c.template == "A_LEFT_OF_B" and len(c.entities) == 2:
        left_e, right_e = (
            (c.entities[0], c.entities[1]) if c.expected is True else (c.entities[1], c.entities[0])
        )
        return f"- [{c.check_id}] ORDER @ {t_label}: KEEP {left_e} left of {right_e}"
    return f"- [{c.check_id}] {c.template} @ {t_label} entities={c.entities} expected={c.expected}"


def build_correction_prompt(
    scene: SceneV1,
    failed: Sequence[CheckV1],
    passed: Sequence[CheckV1],
    frame_labels: Sequence[str] = (),
) -> str:
    """Render `godshot/prompts/correct.md` with failed table, passed summary, and current scene JSON."""
    template_text = PROMPT_PATH.read_text(encoding="utf-8")

    header = (
        "| id | template | mmss | instruction | expected_by_prev | observed | p |\n"
        "|---|---|---|---|---|---|---|"
    )
    rows = [header]
    for c in failed:
        obs, p_str = _format_observed(c)
        t_label = c.mmss if c.t_end is None else f"{c.mmss}–{mmss(c.t_end)}"
        rows.append(
            f"| {c.check_id} | {c.template} | {t_label} | {c.question.instructions} "
            f"| {c.expected} | {obs} | {p_str} |"
        )
    failed_table = "\n".join(rows) if failed else "(none)"

    passed_lines = [_compact_passed_line(c) for c in passed]
    passed_list = "\n".join(passed_lines) if passed_lines else "(none)"
    frame_block = "\n".join(f"- {lbl}" for lbl in frame_labels) if frame_labels else "(no frames attached)"

    return template_text.format(
        frame_labels=frame_block,
        failed_table=failed_table,
        passed_list=passed_list,
        scene_json=scene.model_dump_json(by_alias=True, indent=2),
    )


def _validate_scene_json(raw_text: str, prev: SceneV1) -> SceneV1:
    """Parse JSON and pin `clip` metadata from `prev` so repair calls cannot zero out duration/fps."""
    data = json.loads(raw_text)
    if not isinstance(data, dict):
        raise ValueError("correction response must be a JSON object")
    data["clip"] = prev.clip.model_dump(mode="json")
    return SceneV1.model_validate(data)


def correct(
    scene: SceneV1,
    failed: Sequence[CheckV1],
    passed: Sequence[CheckV1],
    *,
    video_uri: str | None = None,
    frames_dir: Path | None = None,
    clip_bytes: bytes | None = None,
    fps: int | None = None,
    thinking_level: str | None = None,
    return_meta: bool = False,
) -> SceneV1 | tuple[SceneV1, dict[str, Any]]:
    """Call Argon (`MODEL_CORRECT`) to repair failed checks. Cached on disk.

    Uses `HIGH` thinking on main correction rounds (>5 failures) for strong 2D geometry reasoning
    and `MEDIUM` thinking on small cleanup rounds (<=5 failures) to avoid 400s+ tail latency.
    """
    cfg = load_config(require_key=True)
    use_fps = fps if fps is not None else cfg.FPS
    use_thinking = thinking_level or ("MEDIUM" if len(failed) <= 5 else "HIGH")
    frame_entries, frame_labels = _collect_failing_frames(failed, frames_dir)
    prompt = build_correction_prompt(scene, failed, passed, frame_labels)

    frame_blobs: list[tuple[str, bytes]] = []
    for label, path in frame_entries:
        if path.exists():
            frame_blobs.append((label, path.read_bytes()))

    params = {"thinking_level": use_thinking, "fps": use_fps, "schema": "scene.v1"}
    key_parts: list[bytes | str] = [
        clip_bytes if clip_bytes is not None else (video_uri or scene.clip.id),
        cfg.MODEL_CORRECT,
        prompt,
        json.dumps(params, sort_keys=True),
        *(blob for _, blob in frame_blobs),
    ]

    def _call() -> dict[str, Any]:
        from google.genai import types

        client = get_client()
        contents: list[Any] = []
        if video_uri:
            contents.append(
                types.Part(
                    file_data=types.FileData(file_uri=video_uri, mime_type="video/mp4"),
                    video_metadata=types.VideoMetadata(fps=use_fps),
                )
            )
        for label, blob in frame_blobs:
            contents.append(label)
            contents.append(types.Part.from_bytes(data=blob, mime_type="image/jpeg"))
        contents.append(prompt)

        gen_cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=SceneV1.model_json_schema(),
            thinking_config=types.ThinkingConfig(thinking_level=use_thinking),
        )
        t0 = time.perf_counter()
        resp = _generate_with_retry(client, model=cfg.MODEL_CORRECT, contents=contents, config=gen_cfg)
        latency_s = round(time.perf_counter() - t0, 3)
        raw_text = (resp.text or "").strip()
        usage = getattr(resp, "usage_metadata", None)
        tokens = int(getattr(usage, "total_token_count", 0) or 0)

        # Retry main call once if Argon returned an empty payload instead of repairing "".
        if not raw_text:
            log.warning("Argon returned empty correction payload; retrying main call once")
            t_empty = time.perf_counter()
            resp = _generate_with_retry(client, model=cfg.MODEL_CORRECT, contents=contents, config=gen_cfg)
            latency_s = round(latency_s + (time.perf_counter() - t_empty), 3)
            raw_text = (resp.text or "").strip()
            usage2 = getattr(resp, "usage_metadata", None)
            tokens += int(getattr(usage2, "total_token_count", 0) or 0)

        # Validate (pinning prev.clip); one JSON repair retry if Argon returned malformed JSON.
        try:
            validated = _validate_scene_json(raw_text, scene)
        except Exception as err:
            log.warning("Argon correction validation failed (%s); attempting 1 repair call", err)
            repair_prompt = (
                "Fix this JSON so it strictly matches the provided SceneV1 JSON schema; "
                f"change nothing else.\n\nValidation error:\n{err}\n\nJSON:\n{raw_text}"
            )
            t1 = time.perf_counter()
            repair_resp = _generate_with_retry(
                client, model=cfg.MODEL_CORRECT, contents=repair_prompt, config=gen_cfg
            )
            latency_s = round(latency_s + (time.perf_counter() - t1), 3)
            repair_usage = getattr(repair_resp, "usage_metadata", None)
            tokens += int(getattr(repair_usage, "total_token_count", 0) or 0)
            validated = _validate_scene_json((repair_resp.text or "").strip(), scene)

        return {
            "scene": validated.model_dump(mode="json", by_alias=True),
            "tokens": tokens,
            "latency_s": latency_s,
        }

    payload = cached(key_parts, _call, kind="correct")
    corrected = SceneV1.model_validate(payload["scene"])
    meta = {"tokens": int(payload.get("tokens", 0)), "latency_s": float(payload.get("latency_s", 0.0))}
    log.info(
        "correct(%s): tokens=%d latency=%.2fs",
        scene.clip.id,
        meta["tokens"],
        meta["latency_s"],
    )
    if return_meta:
        return corrected, meta
    return corrected
