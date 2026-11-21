"""Loop controller: extract → (checks → verify → correct)×≤3, keep-if-better. Used by CLI + web.

Spec: S06 (.claude/specs/S06*.md) — owner: Rishika.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from godshot import verify as _verify_mod
from godshot.checks import frames_needed, make_checks, predict
from godshot.config import load_config
from godshot.correct import correct as _correct_call
from godshot.correct import freeze_identity, mark_low_confidence
from godshot.schema import (
    CheckResult,
    CheckV1,
    QKey,
    SceneV1,
    Verdict,
    qkey,
    save_checks,
)

log = logging.getLogger(__name__)

# Type aliases for injectable dependencies (makes loop fully unit-testable offline).
VerifyFn = Callable[[list[CheckV1], Path], dict[QKey, CheckResult]]
CorrectFn = Callable[
    [SceneV1, list[CheckV1], list[CheckV1]],
    SceneV1 | tuple[SceneV1, dict[str, Any]],
]
MakeChecksFn = Callable[[SceneV1, Path], list[CheckV1]]


# --------------------------------------------------------------------------- S05 bridge helpers
# Uses Siva's godshot.verify.{observed, compare, attach} once S05 lands; falls back to the exact
# S05 spec rules while S05 is unmerged on main.


def observed(raw: CheckResult) -> bool | str | None:
    fn = getattr(_verify_mod, "observed", None)
    if callable(fn):
        try:
            return fn(raw)
        except NotImplementedError:
            pass
    cfg = load_config()
    if raw.p_yes is not None:
        if raw.p_yes >= cfg.PASS_THRESHOLD:
            return True
        if raw.p_yes <= cfg.FAIL_THRESHOLD:
            return False
        return None
    if raw.choice_probs:
        top_k, top_p = max(raw.choice_probs.items(), key=lambda kv: kv[1])
        if top_p >= 0.5 and top_k != "other":
            return top_k
    return None


def compare(pred: bool | str | None, obs: bool | str | None) -> Verdict:
    fn = getattr(_verify_mod, "compare", None)
    if callable(fn):
        try:
            return fn(pred, obs)
        except NotImplementedError:
            pass
    if obs is None:
        return "uncertain"
    return "pass" if pred == obs else "fail"


def attach(
    scene: SceneV1,
    checks: Sequence[CheckV1],
    answers: dict[QKey, CheckResult],
) -> list[CheckV1]:
    fn = getattr(_verify_mod, "attach", None)
    if callable(fn):
        try:
            return list(fn(scene, checks, answers))
        except NotImplementedError:
            pass
    out: list[CheckV1] = []
    for c in checks:
        k = qkey(c)
        raw = answers.get(k)
        if raw is None:
            out.append(c.model_copy(update={"result": None}))
            continue
        verdict = compare(predict(scene, c), observed(raw))
        res = raw.model_copy(update={"verdict": verdict})
        out.append(c.model_copy(update={"result": res}))
    return out


# --------------------------------------------------------------------------- shared question bank


def score_bank(
    scene: SceneV1,
    bank: dict[QKey, CheckV1],
    answers: dict[QKey, CheckResult],
) -> tuple[float, set[QKey], list[QKey]]:
    """Score `scene` against the shared question bank.

    `predict(scene, c) -> None` (abstention, deletion, or visible=False) is always a MISS
    against any usable (confident) observation.
    """
    usable = [k for k in bank if k in answers and observed(answers[k]) is not None]
    hits = {k for k in usable if predict(scene, bank[k]) == observed(answers[k])}
    score = len(hits) / max(len(usable), 1)
    return score, hits, usable


def _add_to_bank(bank: dict[QKey, CheckV1], checks: Sequence[CheckV1]) -> list[CheckV1]:
    """Add checks whose qkey is not yet in `bank`. Bank only grows, never shrinks."""
    added: list[CheckV1] = []
    for c in checks:
        k = qkey(c)
        if k not in bank:
            bank[k] = c
            added.append(c)
    return added


def _verify_unanswered(
    bank: dict[QKey, CheckV1],
    answers: dict[QKey, CheckResult],
    frames_dir: Path,
    verify_fn: VerifyFn,
    on_before_verify: Callable[[list[CheckV1]], None] | None = None,
) -> int:
    """Verify ONLY bank entries not yet in `answers`. Returns count of newly verified questions."""
    pending = [c for k, c in bank.items() if k not in answers]
    if not pending:
        return 0
    if on_before_verify is not None:
        on_before_verify(pending)
    new_answers = verify_fn(pending, frames_dir)
    for k, raw in new_answers.items():
        if k not in answers:
            # Store raw answer strictly scene-independently (verdict=None).
            answers[k] = raw.model_copy(update={"verdict": None})
    return len(pending)


def run_loop(
    scene0: SceneV1,
    frames_dir: Path,
    out_dir: Path,
    *,
    verify_fn: VerifyFn,
    correct_fn: CorrectFn,
    make_checks_fn: MakeChecksFn = make_checks,
    max_rounds: int | None = None,
    on_before_verify: Callable[[list[CheckV1]], None] | None = None,
) -> tuple[SceneV1, list[dict[str, Any]]]:
    """Run the shared-bank verify-and-correct loop starting from baseline `scene0`."""
    cfg = load_config()
    rounds_limit = cfg.MAX_ROUNDS if max_rounds is None else max_rounds
    out_dir.mkdir(parents=True, exist_ok=True)

    bank: dict[QKey, CheckV1] = {}
    answers: dict[QKey, CheckResult] = {}
    loop_log: list[dict[str, Any]] = []

    # Round 0: baseline checks + verification
    t_r0 = time.perf_counter()
    _add_to_bank(bank, make_checks_fn(scene0, frames_dir))
    _verify_unanswered(bank, answers, frames_dir, verify_fn, on_before_verify)
    s0, _hits0, usable0 = score_bank(scene0, bank, answers)

    scene0.save(out_dir / "scene_r0.json")
    save_checks(attach(scene0, list(bank.values()), answers), out_dir / "checks_r0.json")

    loop_log.append(
        {
            "round": 0,
            "bank_size": len(bank),
            "usable": len(usable0),
            "score_prev": s0,
            "score_cand": s0,
            "regressions": {"count": 0, "check_ids": [], "qkeys": []},
            "accepted": True,
            "changes": scene0.model_notes,
            "tokens": 0,
            "latency_s": round(time.perf_counter() - t_r0, 3),
        }
    )

    current = scene0
    for r in range(1, rounds_limit + 1):
        t_round = time.perf_counter()
        _, hits_before, usable_before = score_bank(current, bank, answers)
        failing_keys = [k for k in usable_before if k not in hits_before]
        if not failing_keys:
            log.info("loop(%s): stop before round %d (no fails)", scene0.clip.id, r)
            break

        attached_now = {qkey(c): c for c in attach(current, list(bank.values()), answers)}
        failed_checks = [attached_now[k] for k in failing_keys]
        passed_checks = [attached_now[k] for k in usable_before if k in hits_before]

        try:
            raw_res = correct_fn(current, failed_checks, passed_checks)
        except Exception as err:
            log.warning(
                "loop(%s) round %d: correction failed (%s); keeping last accepted scene",
                scene0.clip.id,
                r,
                err,
            )
            loop_log.append(
                {
                    "round": r,
                    "bank_size": len(bank),
                    "usable": len(usable_before),
                    "score_prev": round(len(hits_before) / max(len(usable_before), 1), 4),
                    "score_cand": round(len(hits_before) / max(len(usable_before), 1), 4),
                    "regressions": {"count": 0, "check_ids": [], "qkeys": []},
                    "accepted": False,
                    "changes": f"error: {type(err).__name__}: {err}",
                    "tokens": 0,
                    "latency_s": round(time.perf_counter() - t_round, 3),
                }
            )
            break
        if isinstance(raw_res, tuple):
            cand_raw, meta = raw_res
        else:
            cand_raw, meta = raw_res, {}

        cand = freeze_identity(cand_raw, current)

        # Accumulate candidate checks into the shared bank and verify only unanswered qkeys.
        _add_to_bank(bank, make_checks_fn(cand, frames_dir))
        _verify_unanswered(bank, answers, frames_dir, verify_fn, on_before_verify)

        # Score BOTH prev and cand on the SAME updated bank.
        s_prev, hits_prev, usable = score_bank(current, bank, answers)
        s_cand, hits_cand, _ = score_bank(cand, bank, answers)
        regressions = hits_prev - hits_cand
        reg_check_ids = sorted(bank[k].check_id for k in regressions)
        reg_qkeys = sorted([list(k[0]), k[1]] for k in regressions)

        accepted = (s_cand > s_prev) and (len(regressions) <= 1)
        round_latency = round(time.perf_counter() - t_round, 3)

        loop_log.append(
            {
                "round": r,
                "bank_size": len(bank),
                "usable": len(usable),
                "score_prev": round(s_prev, 4),
                "score_cand": round(s_cand, 4),
                "regressions": {
                    "count": len(regressions),
                    "check_ids": reg_check_ids,
                    "qkeys": reg_qkeys,
                },
                "accepted": accepted,
                "changes": cand.model_notes,
                "tokens": int(meta.get("tokens", 0)),
                "latency_s": float(meta.get("latency_s", round_latency)),
            }
        )
        log.info(
            "loop(%s) round %d: bank=%d usable=%d score %.3f -> %.3f regressions=%d accepted=%s",
            scene0.clip.id,
            r,
            len(bank),
            len(usable),
            s_prev,
            s_cand,
            len(regressions),
            accepted,
        )

        if accepted:
            current = cand
            current.save(out_dir / f"scene_r{r}.json")
            save_checks(attach(current, list(bank.values()), answers), out_dir / f"checks_r{r}.json")
        else:
            break

    # Mark remaining failing checks as low_confidence on the final accepted scene.
    _, final_hits, final_usable = score_bank(current, bank, answers)
    attached_final = {qkey(c): c for c in attach(current, list(bank.values()), answers)}
    still_failing = [attached_final[k] for k in final_usable if k not in final_hits]
    final_scene = mark_low_confidence(current, still_failing)

    final_scene.save(out_dir / "scene_final.json")
    (out_dir / "loop_log.json").write_text(json.dumps(loop_log, indent=2) + "\n", encoding="utf-8")
    return final_scene, loop_log


def run(
    clip: Path,
    out_dir: Path,
    *,
    max_rounds: int | None = None,
    render_reel: bool = True,
) -> SceneV1:
    """End-to-end clip pipeline: ingest → extract → verify/correct loop → optional render/compose."""
    from godshot import compose, extract, ingest, render

    clip_path = Path(clip)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    frames_dir = Path("frames") / clip_path.stem

    t_total = time.perf_counter()
    video_uri = ingest.upload(clip_path)
    clip_bytes = clip_path.read_bytes()
    scene0 = extract.extract(clip_path)

    def _extract_needed_frames(pending: list[CheckV1]) -> None:
        ts = frames_needed(pending)
        if not ts:
            return
        safe_max = max(0.0, scene0.clip.duration_s - 0.15)
        normal_ts = [t for t in ts if t <= safe_max]
        eof_ts = [t for t in ts if t > safe_max]
        if normal_ts:
            ingest.frames_at(clip_path, normal_ts, frames_dir)
        if eof_ts:
            # Clamp EOF-edge timestamps (e.g. t == duration_s) so ffmpeg always writes a valid frame.
            fallback = ingest.frame_at(clip_path, safe_max, frames_dir)
            for t in eof_ts:
                target = frames_dir / f"{max(0, round(t * 1000)):06d}.jpg"
                if not target.exists():
                    shutil.copy2(fallback, target)

    def _verify_real(pending: list[CheckV1], fdir: Path) -> dict[QKey, CheckResult]:
        return _verify_mod.verify(pending, fdir)

    def _correct_real(
        prev: SceneV1,
        failed: list[CheckV1],
        passed: list[CheckV1],
    ) -> tuple[SceneV1, dict[str, Any]]:
        res = _correct_call(
            prev,
            failed,
            passed,
            video_uri=video_uri,
            frames_dir=frames_dir,
            clip_bytes=clip_bytes,
            return_meta=True,
        )
        assert isinstance(res, tuple)
        return res

    final_scene, _ = run_loop(
        scene0,
        frames_dir,
        out_path,
        verify_fn=_verify_real,
        correct_fn=_correct_real,
        max_rounds=max_rounds,
        on_before_verify=_extract_needed_frames,
    )

    if render_reel:
        try:
            map_mp4 = render.render_map(final_scene, out_path / "map.mp4")
            compose.compose_reel(clip_path, map_mp4, out_path / "reel.mp4")
        except NotImplementedError:
            log.info("S07/S08 render/compose not implemented yet; skipping reel generation.")

    log.info("pipeline.run(%s) finished in %.2fs", clip_path.name, time.perf_counter() - t_total)
    return final_scene
