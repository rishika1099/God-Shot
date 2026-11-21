# S00: Foundation (config, schema, cache, CLI, mocks)

- **Owner:** Prajesh
- **Status:** todo
- **Depends on:** none
- **Target:** 12:30 (M0). Everyone else is waiting on this, so keep it minimal.
- **Models:** none (one smoke-test call each to Argon and Decisions)

## Goal
Shared foundation so all four people can build in parallel against frozen contracts and a mock scene.

## Files
- `godshot/__init__.py`, `godshot/__main__.py` (typer app with stub commands `run`, `baseline`, `eval`, `web`, `smoke`)
- `godshot/config.py`
- `godshot/schema.py`
- `godshot/cache.py`
- `mocks/scene_mock.json`, `mocks/checks_mock.json`
- `tests/test_schema.py`
- `pyproject.toml` (ruff config, package metadata)

## Spec
- `config.py`: load `.env` with python-dotenv; expose a frozen dataclass `Config` with `API_KEY`, `MODEL_EXTRACT`, `MODEL_CORRECT`, `MODEL_CHEAP`, `MODEL_VERIFY`, `MODEL_RESHOOT`, `MODEL_IMAGE`, `FPS`, `MAX_ROUNDS`, `PASS_THRESHOLD`, `FAIL_THRESHOLD`, `CACHE_DIR`. Provide `get_client()` returning a `genai.Client(http_options={"api_version": "v1beta"})`. Raise a clear error if the key is missing.
- `schema.py`: pydantic v2 models exactly matching `.claude/docs/contracts.md`: `SceneV1` (+ nested `Clip`, `Room`, `Obj`, `Actor`, `ActorKey`, `Camera`, `CamKey`, `MotionSegment`, `LowConf`), `CheckV1` (+ `InputRef`, `Question`, `CheckResult`), `LabelV1` (+ `Label`). Use `Literal` enums. Helpers: `mmss(t: float) -> str`, `SceneV1.load(path)`, `.save(path)`.
- `cache.py`: `cached(key_parts: list[bytes|str], fn: Callable[[], T], kind: str) -> T` storing JSON (or bytes) under `CACHE_DIR/<kind>/<sha256>.json`. Env `GODSHOT_OFFLINE=1` → raise on a cache miss instead of calling the API.
- `mocks/scene_mock.json`: a hand-written, valid `scene.v1` for a 10 s clip: 3 objects (door, table, window), 1 actor walking left→right, camera doing `dolly_in` 0–4 s then `pan_left` 4–8 s, then static.
- `mocks/checks_mock.json`: about 10 `check.v1` items consistent with the mock scene, results filled in, 2 failing.
- `godshot smoke`: list models (print IDs containing "gemini-4", "decision", "omni"), and make one tiny Argon text call and one Decisions `noul` call on a text input. Print OK/FAIL for each.

## Acceptance criteria
- [ ] `python -m godshot smoke` prints OK for Argon and Decisions with the team's `.env`.
- [ ] `pytest tests/test_schema.py` passes: both mocks validate; round-trip save/load is lossless.
- [ ] `.claude/docs/models.md` is updated with the actually verified model IDs.
- [ ] Specs index row is set to done; teammates are told "contracts frozen."

## Notes / changelog
-
