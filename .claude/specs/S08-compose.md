# S08: Compose (9:16 reel)

- **Owner:** Akash
- **Status:** done (phone playback to confirm by hand)
- **Depends on:** S07, S01
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
- [x] A reel from a dev clip + the mock map plays on a phone (h264, yuv420p): format verified (H.264 High, yuv420p, `+faststart`, AAC); **actual playback on a phone still to be checked by hand** (AirDrop `out/<id>/reel.mp4`).
- [x] A 10 s reel composes in < 10 s: about 1.2 s (11 s 1080p clip + map, title, end card); asserted in `test_reel_with_audio_title_and_credit`.
- [x] The credit end card is present when `credit` is passed: +1.5 s black card with the wrapped credit (pixel-tested), absent without `credit`.

## Notes / changelog
- 2026-11-21 (Akash): `godshot/compose.py` + `tests/test_compose.py` (8 tests, synthetic lavfi clips; the ffmpeg tests skip without ffmpeg). Stacked on `akash/s07-render` (the tests render the mock map with S07).
- **Deviation: no `drawtext`.** The Homebrew ffmpeg build has no freetype (same as S01 noted), so the title bar (1080×110, translucent black, 48 px) and the end card (1080×1920 black, credit wrapped at 36 chars, a small "made with God-Shot") are drawn with Pillow as PNGs and fed to ffmpeg as inputs (`overlay` / looped image + `concat`).
- One ffmpeg call per reel. Top: `scale=…:force_original_aspect_ratio=decrease,pad=1080:960` (letter-/pillarbox, black). Bottom: map scaled to 1080×960. `vstack=inputs=2:shortest=1`, constant output fps = the clip's fps, x264 `veryfast` crf 20, yuv420p, `+faststart`.
- **Audio:** kept only if the clip has an audio stream. It is resampled to 48 kHz stereo and padded/trimmed to exactly the stacked video's length (`min(clip, map)`), so the silent end card (`anullsrc`) stays in sync. (A plain `-map 0:a?` would drift when the clip's audio is longer or shorter than the video.)
- `compose_compare(clip_a, clip_b, map_mp4, out)`: 540×960 each side by side, with "ORIGINAL" / "RE-SHOOT" pill labels; map below; ends at the shortest input; audio from `clip_a` only; no title or end card (S12 can add them later if needed).
- Errors: `ComposeError` (ffmpeg/ffprobe missing or failed; the message includes the stderr tail).
