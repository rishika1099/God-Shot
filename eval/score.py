"""Score scene JSON against HUMAN labels (label.v1) using geometry.py, plus verifier-vs-human.

Spec: S11 (.claude/specs/S11*.md) — owner: Siva.

- `score_scene(scene, label)`: every human label evaluated on a scene with `godshot.geometry`.
  This is the HEADLINE metric (baseline scene_r0 vs loop scene_final). No model involved.
- `verifier_vs_human(...)`: asks each askable human label as a Decisions question (`verify.ask`)
  to measure the verifier itself. Written to `eval/runs/verifier_vs_human_<split>.json`, which
  `eval/report.ipynb` reads (fields: p_yes, truth_bool, correct).

CLI (from the repo root):
    python eval/score.py summary --split dev       # quick baseline vs full accuracy in the terminal
    python eval/score.py vvh --split dev           # verifier vs human (needs API key + ffmpeg)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from godshot import geometry as g  # noqa: E402
from godshot.schema import Label, LabelV1, Obj, Question, SceneV1, load_labels  # noqa: E402

log = logging.getLogger(__name__)

# Same thresholds as S04 (godshot/checks.py) so "what counts as a move" matches the checks.
DOLLY_MIN_REL_DIST = 0.15  # forward move > 15% of mean scene distance
TRUCK_MIN_REL_DIST = 0.15  # lateral move > 15% of mean scene distance (no S04 template; same rule as DOLLY)
PAN_MIN_DYAW_DEG = 10.0
MIN_SCENE_DIST = 1.0  # floor so an empty/degenerate scene doesn't make every wobble a "move"

LABELS_PATH = REPO_ROOT / "eval" / "labels" / "labels.jsonl"
CLIPS_CSV = REPO_ROOT / "eval" / "clips.csv"
RUNS_DIR = REPO_ROOT / "eval" / "runs"


# --------------------------------------------------------------------------- camera motion classes


def classify_translation(m: dict[str, float]) -> str:
    """camera_motion dict → translation enum (static | dolly_* | truck_* | arc_*).

    The dominant axis wins. A lateral move with a pan the other way is an arc around the subject
    (S10, verified on CameraBench: arc_left = moves to its left + pans right; arc_right = mirror).
    """
    d = max(m["mean_scene_dist"], MIN_SCENE_DIST)
    fwd, lat, dyaw = m["forward"], m["lateral"], m["dyaw"]
    moved_fwd = abs(fwd) > DOLLY_MIN_REL_DIST * d
    moved_lat = abs(lat) > TRUCK_MIN_REL_DIST * d
    if not (moved_fwd or moved_lat):
        return "static"
    if moved_lat and abs(lat) >= abs(fwd):
        # moving right (lat>0) while turning left (dyaw>0) keeps a subject centred → orbit
        if abs(dyaw) > PAN_MIN_DYAW_DEG and (lat > 0) == (dyaw > 0):
            return "arc_right" if lat > 0 else "arc_left"
        return "truck_right" if lat > 0 else "truck_left"
    return "dolly_in" if fwd > 0 else "dolly_out"


def classify_rotation(m: dict[str, float]) -> str:
    """camera_motion dict → rotation enum (none | pan_left | pan_right). + dyaw = turned left."""
    if m["dyaw"] > PAN_MIN_DYAW_DEG:
        return "pan_left"
    if m["dyaw"] < -PAN_MIN_DYAW_DEG:
        return "pan_right"
    return "none"


# --------------------------------------------------------------------------- per-label scoring


class _Unresolved(Exception):
    pass


def _entity_id(label_rec: LabelV1, scene: SceneV1, desc: str | None) -> str:
    """Human description → scene entity id via `entity_map` only (spec: no fuzzy matching)."""
    eid = label_rec.entity_map.get(desc or "")
    if not eid:
        raise _Unresolved(f"no entity_map entry for {desc!r}")
    try:
        scene.entity(eid)
    except KeyError as e:
        raise _Unresolved(f"entity {eid!r} not in scene") from e
    return eid


def _pos(scene: SceneV1, eid: str, t: float) -> tuple[tuple[float, float], bool]:
    """(x, y) of an entity at t and whether the scene says it can be seen (actor visible flag)."""
    e = scene.entity(eid)
    if isinstance(e, Obj):
        return (e.center[0], e.center[1]), True
    x, y, _, visible = g.interp_actor(e, t)
    return (x, y), visible


def _onscreen(scene: SceneV1, eid: str, t: float) -> tuple[tuple[float, float], bool]:
    p, visible = _pos(scene, eid, t)
    cam = g.interp_camera(scene, t)
    return p, visible and g.in_view(cam, p, scene.camera.hfov_deg)


def _predict_label(scene: SceneV1, label_rec: LabelV1, lb: Label) -> Any:
    """What the scene claims for one human label, in the label's own value vocabulary."""
    if lb.type in ("CAM_TRANSLATION", "CAM_ROTATION"):
        assert lb.t0 is not None and lb.t1 is not None
        m = g.camera_motion(scene, lb.t0, lb.t1)
        return classify_translation(m) if lb.type == "CAM_TRANSLATION" else classify_rotation(m)

    if lb.type == "TOWARD":
        assert lb.t0 is not None and lb.t1 is not None
        eid = _entity_id(label_rec, scene, lb.subject)
        d0 = g.distance(g.interp_camera(scene, lb.t0), _pos(scene, eid, lb.t0)[0])
        d1 = g.distance(g.interp_camera(scene, lb.t1), _pos(scene, eid, lb.t1)[0])
        return d1 < d0

    assert lb.t is not None
    cam = g.interp_camera(scene, lb.t)
    hfov = scene.camera.hfov_deg
    if lb.type == "SIDE":
        p, seen = _onscreen(scene, _entity_id(label_rec, scene, lb.subject), lb.t)
        if not seen:
            return "offscreen"  # humans label SIDE only for visible things, so this counts as wrong
        return "left" if g.screen_x(cam, p, hfov) < 0.5 else "right"

    ea, eb = _entity_id(label_rec, scene, lb.a), _entity_id(label_rec, scene, lb.b)
    pa, pb = _pos(scene, ea, lb.t)[0], _pos(scene, eb, lb.t)[0]
    if lb.type == "A_LEFT_OF_B":
        return g.screen_x(cam, pa, hfov) < g.screen_x(cam, pb, hfov)
    if lb.type == "CLOSER":
        return "a" if g.distance(cam, pa) < g.distance(cam, pb) else "b"
    raise ValueError(f"unknown label type {lb.type}")


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def score_scene(scene: SceneV1, label: LabelV1) -> list[dict[str, Any]]:
    """Evaluate every human label on `scene` with geometry.

    Returns one dict per label: label_id, type, pred, truth, correct, unresolved.
    An entity that can't be resolved through `entity_map` → correct=False, unresolved=True
    (counted as a miss and reported separately).
    """
    out = []
    for lb in label.labels:
        try:
            pred = _predict_label(scene, label, lb)
            unresolved = False
        except _Unresolved as e:
            log.debug("%s/%s unresolved: %s", label.clip_id, lb.label_id, e)
            pred, unresolved = None, True
        truth = _as_bool(lb.value) if lb.type in ("A_LEFT_OF_B", "TOWARD") else _norm(lb.value)
        out.append(
            {
                "label_id": lb.label_id,
                "type": lb.type,
                "pred": pred,
                "truth": truth,
                "correct": (not unresolved) and _norm(pred) == truth,
                "unresolved": unresolved,
            }
        )
    return out


# --------------------------------------------------------------------------- verifier vs human

# label → (S04 template, descriptions, t, t_end, truth in the question's answer vocabulary)
Askable = tuple[str, list[str], float, float | None, bool | str]


def _as_bool(v: bool | str) -> bool:
    """Label truth as a bool. A string "false"/"no"/"0" is False (plain bool() would make it True)."""
    if isinstance(v, bool):
        return v
    return v.strip().lower() in ("true", "yes", "1")


def askable(lb: Label) -> Askable | None:
    """Turn a human label into the S04 question it corresponds to, or None if there is none.

    Skipped: CAM_ROTATION "none" and CAM_TRANSLATION other than dolly_* (no S04 template asks them).
    """
    v = _norm(lb.value)
    if lb.type == "SIDE" and lb.subject and lb.t is not None:
        return "SIDE_LEFT", [lb.subject], lb.t, None, v == "left"
    if lb.type == "A_LEFT_OF_B" and lb.a and lb.b and lb.t is not None:
        return "A_LEFT_OF_B", [lb.a, lb.b], lb.t, None, _as_bool(lb.value)
    if lb.type == "CLOSER" and lb.a and lb.b and lb.t is not None:
        return "CLOSER", [lb.a, lb.b], lb.t, None, "a_first" if v == "a" else "b_second"
    if lb.type == "TOWARD" and lb.subject and lb.t0 is not None and lb.t1 is not None:
        return "TOWARD", [lb.subject], lb.t0, lb.t1, _as_bool(lb.value)
    if lb.type == "CAM_ROTATION" and v in ("pan_left", "pan_right"):
        return "PAN", [], lb.t0 or 0.0, lb.t1, v
    if lb.type == "CAM_TRANSLATION" and v in ("dolly_in", "dolly_out"):
        return "DOLLY", [], lb.t0 or 0.0, lb.t1, v
    return None


def _default_question_builder() -> Callable[..., Question]:
    """S04's question builder, so the verifier is asked exactly the pipeline's wording."""
    from godshot import checks

    fn = getattr(checks, "build_question", None) or getattr(checks, "_build_question", None)
    if fn is None:
        raise RuntimeError("godshot.checks has no build_question (S04 not merged yet)")
    return fn


def _context(t: float, t_end: float | None) -> str:
    from godshot.schema import mmss

    if t_end is None:
        return f"A single frame from one continuous film shot, at {mmss(t)}."
    return f"Image 1 is at {mmss(t)}, Image 2 is at {mmss(t_end)}, same continuous shot."


def _read_clips(path: Path = CLIPS_CSV) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def verifier_vs_human(
    labels: Iterable[LabelV1],
    clips: dict[str, dict[str, str]],
    frames_root: Path,
    build_question: Callable[..., Question] | None = None,
    ask: Callable[..., Any] | None = None,
    frames_at: Callable[..., list[Path]] | None = None,
) -> list[dict[str, Any]]:
    """Ask every askable human label as a Decisions question; one row per label.

    Row: clip_id, label_id, type, template, p_yes, choice_probs, truth, truth_bool, observed, correct,
    uncertain. `correct` = the verifier's confident answer equals the human truth (uncertain → False).
    """
    from godshot import verify

    build = build_question or _default_question_builder()
    ask_fn = ask or verify.ask
    if frames_at is None:
        from godshot.ingest import frames_at as _fa

        frames_at = _fa

    rows: list[dict[str, Any]] = []
    for rec in labels:
        clip = clips.get(rec.clip_id)
        if clip is None:
            log.warning("vvh: %s not in clips.csv, skipped", rec.clip_id)
            continue
        clip_path = REPO_ROOT / clip["path"]
        dur = float(clip.get("duration_s") or 0) or None
        for lb in rec.labels:
            a = askable(lb)
            if a is None:
                continue
            template, descs, t, t_end, truth = a
            if dur:  # frame_at raises past the end; CameraBench labels span the whole clip
                t = min(t, max(0.0, dur - 0.05))
                t_end = None if t_end is None else min(t_end, max(0.0, dur - 0.05))
            q = build(template, descs, t, t_end)
            ts = [t] if t_end is None else [t, t_end]
            imgs = frames_at(clip_path, ts, frames_root / rec.clip_id)
            raw = ask_fn(q, list(imgs), _context(t, t_end))
            obs = verify.observed(raw)
            rows.append(
                {
                    "clip_id": rec.clip_id,
                    "source": rec.source,
                    "label_id": lb.label_id,
                    "type": lb.type,
                    "template": template,
                    "p_yes": raw.p_yes,
                    "choice_probs": raw.choice_probs,
                    "truth": truth,
                    "truth_bool": truth if isinstance(truth, bool) else None,
                    "observed": obs,
                    "correct": obs is not None and obs == truth,
                    "uncertain": obs is None,
                }
            )
    return rows


# --------------------------------------------------------------------------- terminal summary


def summarize_runs(labels: Iterable[LabelV1], runs_dir: Path = RUNS_DIR) -> dict[str, dict[str, Any]]:
    """Accuracy per system (baseline = scene_r0, full = scene_final) over clips that have BOTH runs."""
    systems = (("baseline", "scene_r0.json"), ("full", "scene_final.json"))
    labels = list(labels)
    # Paired comparison: only clips that have BOTH outputs, so the two accuracies cover the same clips.
    paired = [rec for rec in labels if all((runs_dir / s / rec.clip_id / f).exists() for s, f in systems)]
    out: dict[str, dict[str, Any]] = {}
    for system, fname in systems:
        n = correct = unresolved = clips = 0
        by_type: dict[str, list[int]] = {}
        for rec in paired:
            p = runs_dir / system / rec.clip_id / fname
            clips += 1
            for r in score_scene(SceneV1.load(p), rec):
                n += 1
                correct += r["correct"]
                unresolved += r["unresolved"]
                by_type.setdefault(r["type"], [0, 0])
                by_type[r["type"]][0] += r["correct"]
                by_type[r["type"]][1] += 1
        out[system] = {
            "clips": clips,
            "n": n,
            "accuracy": correct / n if n else None,
            "unresolved": unresolved,
            "by_type": {k: c / t for k, (c, t) in sorted(by_type.items())},
            "unpaired_skipped": len(labels) - len(paired),
        }
    return out


def _labels_for(split: str, path: Path = LABELS_PATH) -> list[LabelV1]:
    """Primary label record per clip in the split (first one; double labels are for agreement only)."""
    seen: dict[str, LabelV1] = {}
    for rec in load_labels(path):
        if rec.split == split:
            seen.setdefault(rec.clip_id, rec)
    return list(seen.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["summary", "vvh"])
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--labels", type=Path, default=LABELS_PATH)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    labels = _labels_for(args.split, args.labels)
    if args.command == "summary":
        print(json.dumps(summarize_runs(labels), indent=2))
        return 0

    rows = verifier_vs_human(labels, _read_clips(), RUNS_DIR / "frames")
    out = RUNS_DIR / f"verifier_vs_human_{args.split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    decided = [r for r in rows if not r["uncertain"]]
    acc = sum(r["correct"] for r in rows) / len(rows) if rows else float("nan")
    print(f"wrote {out} ({len(rows)} questions, {len(decided)} confident, accuracy {acc:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
