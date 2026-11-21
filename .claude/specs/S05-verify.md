# S05: Verify (Decisions API client)

- **Owner:** Siva
- **Status:** todo
- **Depends on:** S04, S01
- **Target:** 2:45
- **Models:** `MODEL_VERIFY` = gemini-decision-1 via `client.decisions.create` (`api_version="v1beta"`). VERIFY the request/response field names in the docs first.

## Goal
Answer every check against the real frames, fast and in batches, and set a verdict.

## Files
- `godshot/verify.py`
- `tests/test_verify.py` (offline, with recorded responses)

## Spec
- `verify(checks: list[CheckV1], frames_dir: Path) -> list[CheckV1]` (with `result` filled).
- Batching: group checks by `input_ref.paths`, so there's **one request per frame or frame pair**. Input = a short text context ("A single frame from one continuous film shot, at {mmss}." / "Image 1 at {t0}, Image 2 at {t1}, same continuous shot.") + image block(s) (inline base64 JPEG, ≤768 px wide).
- Run requests concurrently (ThreadPool, 8 workers). Retry once on 429/5xx with backoff.
- Verdict for `noul`: expected=true → pass if `p_yes ≥ PASS_THRESHOLD` (0.7), fail if `≤ FAIL_THRESHOLD` (0.3), else uncertain. Mirror it for expected=false.
- Verdict for `choice`: pass if the expected key has the top probability and ≥ 0.5; fail if a *different non-`other`* key is top with ≥ 0.5; else uncertain.
- Cache by (image bytes hash + instructions + model).
- `summary(checks) -> {"n": , "pass": , "fail": , "uncertain": , "pass_rate": , "by_template": {...}}`. pass_rate = pass / (pass + fail).
- Also expose `ask(question: Question, images: list[Path], context: str) -> CheckResult` for S11 (verifier vs human) and S12.

## Acceptance criteria
- [ ] Verifies the mock checks on 2 real dev-clip frames end to end; latency per request is logged.
- [ ] A 50-check clip verifies in < 10 s wall time.
- [ ] The threshold values come from config; tuning on dev clips is recorded in Notes (final values + why).
- [ ] Mirrored controls report yes-bias = mean p_yes on control checks.

## Notes / changelog
-
