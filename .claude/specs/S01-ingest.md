# S01: Ingest (upload + frames)

- **Owner:** Prajesh
- **Status:** done
- **Depends on:** S00
- **Models:** Gemini Files API (no generation)

## Goal
Upload a clip once and reuse its URI everywhere. Extract exact-timestamp frames for checks and corrections.

## Files
- `godshot/ingest.py`
- `tests/test_ingest.py`

## Spec
- `probe(clip: Path) -> Clip`: ffprobe for duration, fps, width, height; `id` = filename stem.
- `upload(clip: Path) -> str`: Files API upload, poll until `ACTIVE` (timeout 120 s), return the URI. Cache by clip sha256 (URIs expire, so store the timestamp and re-upload if older than 40 h).
- `frame_at(clip: Path, t: float, out_dir: Path, max_w: int = 768) -> Path`: `ffmpeg -ss {t} -i clip -frames:v 1 -vf scale={max_w}:-2 -q:v 2 out_dir/{int(t*1000):06d}.jpg`. Idempotent (skip if the file exists).
- `frames_at(clip, ts: list[float], out_dir) -> list[Path]`: parallel (ThreadPool, 4 workers).
- `trim(clip, t0, t1, out) -> Path`: for stress tests and Omni references.
- Reject clips > 35 s with a clear error; warn above 15 s.

## Acceptance criteria
- [x] `frame_at` returns a frame matching the timestamp to within 1 frame (test with a generated test pattern clip that has the frame number burned in via ffmpeg `drawtext`).
- [x] A second `upload` of the same clip hits the cache (no network).
- [x] 40 frames are extracted in under 5 s on a laptop.

## Notes / changelog
- **Built (2026-11-21):** `godshot/ingest.py`: `probe` (→ `schema.Clip`), `upload` (Files API, polls to ACTIVE, 120 s timeout), `frame_at` / `frames_at` (4 workers, input order kept, duplicates extracted once), `trim` (re-encode, frame-accurate), plus `frame_name(t)` and `sha256_file`. Errors: `IngestError`, `ClipTooLongError` (> 35 s); warning above 15 s. `probe(check_length=False)` skips the limits (for eval tooling).
- **Tests:** `tests/test_ingest.py`, 22 tests, all pass, ~2 s. ffmpeg tests skip if ffmpeg is missing. Frame accuracy is exact on a synthetic clip with a long GOP + B-frames at 9 timestamps; 40 frames from a 720p clip in < 5 s; upload logic uses a fake client (cache hit, 40 h expiry, offline mode, FAILED, timeout).
- **Live check:** a real 2 s clip uploaded in 5.5 s to ACTIVE; the second `upload` hit the cache (0 ms, same URI).
- **Deviations:**
  - Frame names use `round(t*1000)`, not `int(t*1000)` (`int(2.3*1000) == 2299`). Matches `make_check_id`/mmss rounding.
  - The accuracy test encodes the frame number as an 8-bit bar code via `geq`, not `drawtext`: the Homebrew ffmpeg has no freetype, and a bar code can be read back exactly without OCR.
- **Gotchas:**
  - `frame_at` writes to `.tmp-<name>.jpg` and renames, so a crash never leaves a half-written frame; it raises `IngestError` past the end of the clip (clamp `t` in callers).
  - Upload records live in `.cache/uploads/<clip sha256>.json` (uri, name, mime_type, uploaded_at, expiration_time). Fresh = < 40 h old and > 1 h before the API's expiry. With `GODSHOT_OFFLINE=1` a stale URI is still returned (downstream calls come from cache); never uploaded → `CacheMiss`.
  - Needs ffmpeg + ffprobe on PATH (`brew install ffmpeg`).
