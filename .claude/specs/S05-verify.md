# S05: Verify (Decisions API client)

- **Owner:** Siva
- **Status:** in progress (code + offline tests done; live run needs `.env` + real dev frames)
- **Depends on:** S04, S01
- **Models:** `MODEL_VERIFY` = gemini-decision-1 via `client.decisions.create` (`api_version="v1beta"`). VERIFY the request/response field names in the docs first.

## Goal
Answer every question against the real frames, fast and in batches. Store RAW answers (scene-independent); verdicts are computed per scene.

## Files
- `godshot/verify.py`
- `tests/test_verify.py` (offline, with recorded responses)

## Spec
- `verify(checks: list[CheckV1], frames_dir: Path) -> dict[QKey, Raw]`: per-question RAW answers keyed by `schema.qkey(c)`, where `Raw = {"p_yes": float | None, "choice_probs": dict[str, float] | None, "confidence": float}` (i.e. a `CheckResult` with `verdict=None`). Checks that share a qkey are asked once. The API's noul field is `noul` → our `p_yes`; choice `probabilities` → our `choice_probs`.
- `observed(raw) -> bool | str | None`: noul → `True` if `p_yes >= PASS_THRESHOLD`, `False` if `p_yes <= FAIL_THRESHOLD`, else `None`; choice → the top key if its prob `>= 0.5` and it isn't `"other"`, else `None`.
- `compare(pred, obs) -> verdict`: `uncertain` if `obs is None`; `pass` if `pred == obs`; otherwise `fail` (including `pred is None`, an abstention, against a confident observation).
- `attach(scene, checks, answers) -> list[CheckV1]`: fills `result` = raw + `verdict = compare(predict(scene, c), observed(raw))` for writing `checks_rN.json`. Verdicts are always relative to ONE scene; never store a verdict as if it were the answer.
- Batching: group checks by `input_ref.paths`, so there's **one request per frame or frame pair**. Input = a short text context ("A single frame from one continuous film shot, at {mmss}." / "Image 1 at {t0}, Image 2 at {t1}, same continuous shot.") + image block(s) (inline base64 JPEG, ≤768 px wide).
- Run requests concurrently (ThreadPool, 8 workers). Retry once on 429/5xx with backoff.
- Verdict rules above reproduce the old behaviour for a check's own scene (`pred == c.expected`): noul expected=true passes at `p_yes ≥ 0.7`, fails at `≤ 0.3`; choice passes when the expected key is top with `≥ 0.5`, fails when a different non-`other` key is.
- Cache raw answers by qkey + model + image bytes: disk key = `sha256(image bytes of every path + instructions + model)` via `godshot.cache.cached`, so a question is never asked twice for a clip (across rounds, candidates and re-runs). Requests still batch all un-answered questions for the same `paths` into one call.
- `summary(checks) -> {"n": , "pass": , "fail": , "uncertain": , "pass_rate": , "by_template": {...}}` over checks with scene-relative verdicts (from `attach`). pass_rate = pass / (pass + fail). For reporting only; S06 accepts/rejects with its own bank score.
- Also expose `ask(question: Question, images: list[Path], context: str) -> CheckResult` (raw, `verdict=None`) for S11 (verifier vs human) and S12.

## Acceptance criteria
- [ ] Verifies the mock checks on 2 real dev-clip frames end to end; latency per request is logged.
- [ ] A 50-check clip verifies in < 10 s wall time.
- [ ] The threshold values come from config; tuning on dev clips is recorded in Notes (final values + why).
- [x] Mirrored controls report yes-bias = mean p_yes on control checks.
- [x] Offline test: verifying the same checks twice makes zero new API calls; two checks with the same qkey cause one question in the request.
- [x] `observed` / `compare` unit tests cover: thresholds at the boundaries, choice top=`other` → `None`, `pred=None` vs confident `obs` → `fail`.

## Notes / changelog
- 2026-11-21 (Siva): implemented `godshot/verify.py` + `tests/test_verify.py` (35 offline tests; a fake client replays
  the answers recorded in `mocks/checks_mock.json` and reproduces every mock verdict).
  - Public API as specced: `verify`, `observed`, `compare`, `attach`, `summary`, `ask`, plus `check_context(check)`
    (the text prefix shown before the images; S12 can reuse it).
  - Additive keyword args (not contract changes): `verify(..., client=None)` and `ask(..., client=None)` for test
    injection; `attach(..., predict=None)` defaults to `godshot.checks.predict` (S04), imported lazily so S05 works
    before S04 lands.
  - Wire format checked in the installed SDK (google-genai 3.6.1, `_gaos/types/decisions`): image block =
    `{"type": "image", "mime_type": "image/jpeg", "data": <base64 str>}`; `model` must be `models/<id>`; answers in
    `resp.answers[qid]` with `.noul` / `.probabilities` + `.confidence`. Request question ids are `q0, q1, ...`
    (the model never sees them).
  - Cache: one entry per QUESTION (kind `decisions`), key = model + full `Question.to_api()` JSON + context text +
    raw image bytes. Slightly stricter than the spec (adds criteria/options and context) so two questions that differ
    only in criteria can't share an answer. Un-cached questions for the same input still go in one request.
    `GODSHOT_OFFLINE=1` → `CacheMiss` on any miss.
  - Frame paths: absolute, else `frames_dir / path`, else `frames_dir / basename`. Missing frame → `FileNotFoundError`.
  - Images ≤ 768 px wide: JPEGs at or under that are sent as-is; anything else is re-encoded/downscaled with Pillow.
  - Errors: the SDK already retries internally; on top of that, one retry on 429/5xx with 2–3 s backoff, then raise.
    Other 4xx (e.g. a wrong model id) raise immediately. A question with no usable answer is logged and left out of the
    result (`attach` → `result=None`, `summary` counts it as uncertain).
  - `summary` adds `yes_bias` (mean p_yes over `control` checks; None if none). `pass_rate` is None when nothing was
    decided.
  - Latency: logged per request (`decisions: N question(s), K image(s) in X s`) and per `verify` call.
  - Still open: the live end-to-end run on 2 real dev-clip frames and the < 10 s wall-time check for a 50-check clip
    need the team `.env` + S01 frames. A fake-latency test shows the 8-worker pool runs 16 requests in parallel.
    Thresholds are still the defaults (0.7 / 0.3) from config; tune on dev clips and record the values here.
