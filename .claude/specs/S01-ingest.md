# S01: Ingest (upload + frames)

- **Owner:** Prajesh
- **Status:** todo
- **Depends on:** S00
- **Target:** 1:00
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
- [ ] `frame_at` returns a frame matching the timestamp to within 1 frame (test with a generated test pattern clip that has the frame number burned in via ffmpeg `drawtext`).
- [ ] A second `upload` of the same clip hits the cache (no network).
- [ ] 40 frames are extracted in under 5 s on a laptop.

## Notes / changelog
-
