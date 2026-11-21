"""Decisions API (gemini-decision-1): answer check questions against real frames.

Spec: S05 (.claude/specs/S05*.md) — owner: Siva.

Shared question bank: `verify()` stores RAW, scene-independent answers keyed by `schema.qkey`.
Verdicts are always relative to ONE scene: `compare(predict(scene, c), observed(raw))`, via `attach()`.

Wire format verified against google-genai 3.6.1 (`google/genai/_gaos/types/decisions`):
- request: `client.decisions.create(model="models/<id>", input=[content blocks], questions={id: q})`
- image block: `{"type": "image", "mime_type": "image/jpeg", "data": <base64 str>}`
- answers: `resp.answers[id]` → noul: `.noul` (P(true)) + `.confidence`;
  choice: `.probabilities` + `.confidence`
"""

from __future__ import annotations

import base64
import io
import json
import logging
import random
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from godshot.cache import cached
from godshot.config import get_client, load_config
from godshot.schema import CheckResult, CheckV1, QKey, Question, Verdict, mmss, qkey

if TYPE_CHECKING:
    from godshot.schema import SceneV1

log = logging.getLogger(__name__)

CACHE_KIND = "decisions"
MAX_W = 768  # px; frames from S01 are already this wide, larger images get downscaled
WORKERS = 8
MAX_QUESTIONS_PER_REQUEST = 1000  # Decisions API limit (models.md)
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_BACKOFF_S = 2.0

Raw = CheckResult  # a CheckResult with verdict=None
Prediction = bool | str | None
PredictFn = Callable[["SceneV1", CheckV1], Prediction]


# --------------------------------------------------------------------------- verdict logic (pure)


def observed(raw: CheckResult | Mapping[str, Any] | None) -> Prediction:
    """What the verifier saw, or None when it isn't confident.

    noul → True if p_yes ≥ PASS_THRESHOLD, False if p_yes ≤ FAIL_THRESHOLD, else None.
    choice → the top key if its prob ≥ 0.5 and it isn't "other", else None.
    """
    if raw is None:
        return None
    r = raw if isinstance(raw, CheckResult) else CheckResult.model_validate(raw)
    cfg = load_config()
    if r.p_yes is not None:
        if r.p_yes >= cfg.PASS_THRESHOLD:
            return True
        if r.p_yes <= cfg.FAIL_THRESHOLD:
            return False
        return None
    if r.choice_probs:
        top = max(r.choice_probs, key=lambda k: r.choice_probs[k])  # type: ignore[index]
        if top != "other" and r.choice_probs[top] >= 0.5:
            return top
    return None


def compare(pred: Prediction, obs: Prediction) -> Verdict:
    """Scene-relative verdict. An abstaining scene (pred=None) fails against a confident observation."""
    if obs is None:
        return "uncertain"
    return "pass" if pred == obs else "fail"


# --------------------------------------------------------------------------- request building


def _resolve(path: str, frames_dir: Path) -> Path:
    """`input_ref.paths` entry → file: absolute, else relative to frames_dir, else its basename there."""
    p = Path(path)
    if p.is_absolute():
        return p
    for cand in (frames_dir / p, frames_dir / p.name):
        if cand.exists():
            return cand
    raise FileNotFoundError(f"frame {path!r} not found under {frames_dir}")


def _jpeg_b64(data: bytes) -> str:
    """Base64 JPEG ≤ MAX_W px wide. Re-encodes only when the image is too wide or not a JPEG."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        if im.format == "JPEG" and im.width <= MAX_W:
            return base64.b64encode(data).decode("ascii")
        im = im.convert("RGB")
        if im.width > MAX_W:
            im = im.resize((MAX_W, max(1, round(im.height * MAX_W / im.width))))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def check_context(check: CheckV1) -> str:
    """Short text prefix shown to the verifier before the image(s)."""
    if check.input_ref.kind == "frame":
        return f"A single frame from one continuous film shot, at {mmss(check.t)}."
    if check.t_end is None:
        return "Image 1 and Image 2 come from the same continuous shot, in that order."
    return f"Image 1 is at {mmss(check.t)}, Image 2 is at {mmss(check.t_end)}, same continuous shot."


def _input_blocks(context: str, images: Sequence[bytes]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": context}]
    blocks += [{"type": "image", "mime_type": "image/jpeg", "data": _jpeg_b64(b)} for b in images]
    return blocks


def _key_parts(model: str, question: Question, context: str, images: Sequence[bytes]) -> list[bytes | str]:
    """Disk-cache key for ONE question: model + full question (instructions + criteria) + context + images."""
    q = json.dumps(question.to_api(), sort_keys=True, ensure_ascii=False)
    return [model, q, context, *images]


def _model_path(model: str) -> str:
    return model if model.startswith("models/") else f"models/{model}"


# --------------------------------------------------------------------------- API call


def _status(e: Exception) -> int | None:
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    return code if isinstance(code, int) else None


def _answer_to_raw(ans: Any) -> dict[str, Any] | None:
    """SDK Answer (or plain dict) → Raw dict, or None for an unknown/empty answer."""
    get = ans.get if isinstance(ans, Mapping) else lambda k, d=None: getattr(ans, k, d)
    conf = get("confidence")
    noul = get("noul")
    probs = get("probabilities")
    if conf is None or (noul is None and not probs):
        return None
    return {
        "p_yes": float(noul) if noul is not None else None,
        "choice_probs": {str(k): float(v) for k, v in dict(probs).items()} if probs else None,
        "confidence": float(conf),
        "verdict": None,
    }


def _call(client: Any, model: str, blocks: list[dict[str, Any]], questions: dict[str, Question]) -> dict:
    """One Decisions request; retries once on 429/5xx. Returns {question_id: Raw dict}."""
    payload = {qid: q.to_api() for qid, q in questions.items()}
    for attempt in (1, 2):
        t0 = time.perf_counter()
        try:
            resp = client.decisions.create(model=_model_path(model), input=blocks, questions=payload)
        except Exception as e:
            if attempt == 1 and _status(e) in RETRY_STATUSES:
                wait = RETRY_BACKOFF_S * random.uniform(1.0, 1.5)
                log.warning("decisions %s, retrying in %.1fs", _status(e), wait)
                time.sleep(wait)
                continue
            raise
        log.info(
            "decisions: %d question(s), %d image(s) in %.2fs",
            len(payload),
            len(blocks) - 1,
            time.perf_counter() - t0,
        )
        answers = getattr(resp, "answers", None) or {}
        out = {}
        for qid in payload:
            raw = _answer_to_raw(answers.get(qid)) if qid in answers else None
            if raw is None:
                log.warning("decisions: no usable answer for question %s", qid)
            else:
                out[qid] = raw
        return out
    raise AssertionError("unreachable")


class _Miss(Exception):
    pass


def _raise_miss() -> Any:
    raise _Miss


def _lookup(key_parts: list[bytes | str]) -> dict[str, Any] | None:
    """Cached raw answer or None. In offline mode `cached` raises CacheMiss instead (on purpose)."""
    try:
        return cached(key_parts, _raise_miss, CACHE_KIND)
    except _Miss:
        return None


def _ask_batch(
    client_fn: Callable[[], Any],
    model: str,
    context: str,
    images: Sequence[bytes],
    questions: Sequence[Question],
) -> list[dict[str, Any] | None]:
    """Answer questions that share one input, from cache where possible; one request for the rest."""
    keys = [_key_parts(model, q, context, images) for q in questions]
    results = [_lookup(k) for k in keys]
    todo = [i for i, r in enumerate(results) if r is None]
    if not todo:
        return results
    blocks = _input_blocks(context, images)
    for start in range(0, len(todo), MAX_QUESTIONS_PER_REQUEST):
        chunk = todo[start : start + MAX_QUESTIONS_PER_REQUEST]
        got = _call(client_fn(), model, blocks, {f"q{i}": questions[i] for i in chunk})
        for i in chunk:
            raw = got.get(f"q{i}")
            if raw is not None:
                results[i] = cached(keys[i], lambda raw=raw: raw, CACHE_KIND)
    return results


# --------------------------------------------------------------------------- public API


def _client_factory(client: Any | None) -> Callable[[], Any]:
    box: list[Any] = [client]
    lock = threading.Lock()

    def get() -> Any:
        with lock:
            if box[0] is None:
                box[0] = get_client()
            return box[0]

    return get


def verify(checks: Iterable[CheckV1], frames_dir: Path, client: Any | None = None) -> dict[QKey, Raw]:
    """Raw verifier answers for every distinct question, keyed by `schema.qkey`.

    One request per frame / frame pair (all of that input's un-cached questions together), run on
    WORKERS threads. Questions that share a qkey are asked once. Unanswered questions are left out.
    `client` is injectable for tests; by default `config.get_client()` is created on first need.
    """
    model = load_config().MODEL_VERIFY
    client_fn = _client_factory(client)

    groups: dict[tuple[str, ...], dict[QKey, CheckV1]] = {}
    for c in checks:
        groups.setdefault(tuple(c.input_ref.paths), {}).setdefault(qkey(c), c)

    def run_group(paths: tuple[str, ...], by_key: dict[QKey, CheckV1]) -> dict[QKey, Raw]:
        images = [_resolve(p, Path(frames_dir)).read_bytes() for p in paths]
        first = next(iter(by_key.values()))
        raws = _ask_batch(
            client_fn, model, check_context(first), images, [c.question for c in by_key.values()]
        )
        return {k: CheckResult.model_validate(r) for k, r in zip(by_key, raws, strict=True) if r is not None}

    t0 = time.perf_counter()
    answers: dict[QKey, Raw] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for part in pool.map(lambda item: run_group(*item), groups.items()):
            answers.update(part)
    n_q = sum(len(g) for g in groups.values())
    log.info(
        "verify: %d/%d questions answered over %d inputs in %.2fs",
        len(answers),
        n_q,
        len(groups),
        time.perf_counter() - t0,
    )
    return answers


def ask(question: Question, images: list[Path], context: str, client: Any | None = None) -> CheckResult:
    """Ask one question about some images (S11 verifier-vs-human, S12 conformance). Raw, verdict=None."""
    model = load_config().MODEL_VERIFY
    blobs = [Path(p).read_bytes() for p in images]
    raw = _ask_batch(_client_factory(client), model, context, blobs, [question])[0]
    if raw is None:
        raise RuntimeError(f"Decisions returned no answer for: {question.instructions[:80]!r}")
    return CheckResult.model_validate(raw)


def _default_predict() -> PredictFn:
    from godshot.checks import predict  # S04 (Rishika); imported lazily so S05 doesn't depend on it at import

    return predict


def attach(
    scene: SceneV1, checks: Iterable[CheckV1], answers: Mapping[QKey, Raw], predict: PredictFn | None = None
) -> list[CheckV1]:
    """Copies of `checks` with `result` = raw answer + verdict for THIS scene (for checks_rN.json).

    verdict = compare(predict(scene, c), observed(raw)). Checks with no answer keep `result=None`.
    `predict` defaults to `godshot.checks.predict` (S04).
    """
    pred_fn = predict or _default_predict()
    out = []
    for c in checks:
        raw = answers.get(qkey(c))
        if raw is None:
            out.append(c.model_copy(update={"result": None}))
            continue
        verdict = compare(pred_fn(scene, c), observed(raw))
        out.append(c.model_copy(update={"result": raw.model_copy(update={"verdict": verdict})}))
    return out


def _counts(checks: Sequence[CheckV1]) -> dict[str, int]:
    v = [c.result.verdict if c.result else None for c in checks]
    return {
        "n": len(checks),
        "pass": v.count("pass"),
        "fail": v.count("fail"),
        "uncertain": len(checks) - v.count("pass") - v.count("fail"),  # includes unanswered
    }


def summary(checks: Iterable[CheckV1]) -> dict[str, Any]:
    """Counts over scene-relative verdicts (from `attach`). For reporting; S06 scores the bank itself.

    pass_rate = pass / (pass + fail), None when nothing was decided. Unanswered checks count as uncertain.
    yes_bias = mean p_yes over mirrored control checks (expected False), None if there are none.
    """
    cs = list(checks)
    out: dict[str, Any] = _counts(cs)
    decided = out["pass"] + out["fail"]
    out["pass_rate"] = out["pass"] / decided if decided else None
    by_t: dict[str, list[CheckV1]] = {}
    for c in cs:
        by_t.setdefault(c.template, []).append(c)
    out["by_template"] = {t: _counts(v) for t, v in sorted(by_t.items())}
    ctrl = [c.result.p_yes for c in cs if c.control and c.result and c.result.p_yes is not None]
    out["yes_bias"] = sum(ctrl) / len(ctrl) if ctrl else None
    return out
