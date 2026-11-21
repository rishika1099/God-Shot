# S08: Compose (9:16 reel)

- **Owner:** Akash
- **Status:** todo
- **Depends on:** S07, S01
- **Target:** 2:30
- **Models:** none

## Goal
Stack clip + map into a shareable 1080×1920 reel with a title and a credits end card.

## Files
- `godshot/compose.py`
- `tests/test_compose.py`

## Spec
- `compose_reel(clip: Path, map_mp4: Path, out: Path, title: str | None, credit: str | None) -> Path`:
  - top: clip scaled/letterboxed into 1080×960; bottom: map 1080×960
  - `vstack=inputs=2:shortest=1`, keep the clip's audio if present (`-map 0:a?`)
  - optional title bar (drawtext) on top of the clip region; a 1.5 s end card with `credit` (license line) on black
- `compose_compare(clip_a, clip_b, map_mp4, out)`: for re-shoots (S12). Original and generated clips side by side on top (540×960 each), map below.
- Use `-preset veryfast`, `-pix_fmt yuv420p`, `-movflags +faststart`.

## Acceptance criteria
- [ ] A reel from a dev clip + the mock map plays on a phone (h264, yuv420p).
- [ ] A 10 s reel composes in < 10 s.
- [ ] The credit end card is present when `credit` is passed.

## Notes / changelog
-
