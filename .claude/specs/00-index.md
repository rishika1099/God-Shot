# Spec board

Update your row when you start (`in progress`) and finish (`done`).

| Spec | Title | Owner | Depends on | Status |
|---|---|---|---|---|
| S00 | Foundation: config, schema, cache, CLI, mocks | Prajesh | none | done |
| S01 | Ingest: upload + frames | Prajesh | S00 | done |
| S02 | Extract: Argon scene JSON (baseline) | Akash | S00, S01 | todo |
| S03 | Geometry: projection math | Siva | S00 | done |
| S04 | Checks: scene → check.v1 | Rishika | S03 | todo |
| S05 | Verify: Decisions API client | Siva | S04, S01 | todo |
| S06 | Correct + loop controller | Rishika | S02, S05 | todo |
| S07 | Render: animated overhead map | Akash | S00 | todo |
| S08 | Compose: 9:16 reel | Akash | S07, S01 | todo |
| S09 | Web UI: upload, view, edit, cached demo | Siva | S08 | todo |
| S10 | Eval data: CameraBench + clips + labels | Rishika | S00 | todo |
| S11 | Scoring + metrics + charts | Siva (score), Rishika (charts) | S03, S10, S06 | todo |
| S12 | Re-shoot with Omni + conformance | Prajesh | S06, S09 | todo |
| S13 | Pitch: deck, demo script, failure gallery, backup video | Rishika (Akash: gallery) | S11 | todo |

## Notes
- 2026-11-21: S04/S06 reassigned to Rishika; S05 stays with Siva.
- 2026-11-21: S02 → Akash; S09 → Siva; S12 fully Prajesh (spike + UI button).
- 2026-11-21: S04/S05/S06 updated: shared question bank. Every scene in a clip is scored on one accumulating bank keyed by `schema.qkey`; S04 adds `predict()`, S05 stores raw answers + `observed()`, S06 has a new acceptance rule. See contracts.md changelog.

- 2026-11-21: S00 done, smoke OK, model IDs verified. **Contracts frozen.**

## Milestones
Milestones are reached in order, not at fixed times.
- **M0:** S00 done → everyone builds against `mocks/scene_mock.json`.
- **M1:** one real clip through S01→S08. All four integrate together.
- **M2:** test-split numbers locked. Go/no-go on S12 polish.
- **Freeze:** no new features; pre-render demo reels; submit.

## Parallel tracks
```
Prajesh  S00 ─► S01 ─► Omni spike ──────────────────────────► S12 (+ UI button)
Akash          S02 ─► S07 ─► S08 ─────────────────────────► S13 gallery
Siva           S03 ─► S05 ─► S09 ──────────────────────────► S11(score)
Rishika        S04 ──(S05)──► S06   + S10 (clips, CameraBench, labels) ─► S11 charts ─► S13
```
