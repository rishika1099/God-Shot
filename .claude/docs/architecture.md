# Architecture

## The one idea
Gemini 4 **proposes** a scene map → our code **derives falsifiable predictions** from it → Gemini Decision 1 **checks** each prediction against the real frames → Gemini 4 **fixes** what failed. Repeat up to 3 times. One loop powers the product, the eval and the re-shoot verification.

## Flow
```
            ┌──────────────── ≤3 rounds, keep only if pass rate improves ─────────────┐
            ▼                                                                         │
clip ─► S01 ingest ─► S02 extract (Argon) ─► scene.json ─► S04 checks ─► S05 verify ─► S06 correct (Argon)
                         │ round 0 = baseline                                  │
                         ▼                                                     ▼
                     eval (S11) ◄──────────────── final scene.json + low_confidence
                                                                               │
                                                       S07 render map ─► S08 reel (clip over map)
                                                                               │
                                       S09 web UI: view, edit map ─► re-render (S07/S08)
                                                                 └─► S12 re-shoot (Omni) ─► S04+S05 vs EDITED map
                                                                                            = conformance score
```

## Components
| Spec | Module | Owner | Model | Purpose |
|---|---|---|---|---|
| S00 | config, schema, cache, CLI, mocks | Prajesh | none | Shared foundation and frozen contracts |
| S01 | `ingest.py` | Prajesh | Files API | Upload clip once, reuse URI; ffmpeg frames at exact timestamps |
| S02 | `extract.py` | Akash | gemini-4-argon (MEDIUM) | Video → `scene.v1`. Round 0 is the BASELINE |
| S03 | `geometry.py` | Siva | none | Camera pose + point → bearing, visibility, screen-x, distances |
| S04 | `checks.py` | Rishika | none | `scene.v1` → `check.v1` list from templates, with abstain margins |
| S05 | `verify.py` | Siva | gemini-decision-1 | Batch checks per frame/pair → p(yes) → pass/fail/uncertain |
| S06 | `correct.py` | Rishika | gemini-4-argon (HIGH) | Failed checks + frames → corrected scene; loop controller |
| S07 | `render.py` | Akash | none | `scene.v1` → map.mp4 synced to the clip |
| S08 | `compose.py` | Akash | none | ffmpeg vstack → 1080×1920 reel + end card |
| S09 | `web/` | Siva | none (calls pipeline) | Upload, run, view, edit map, cached demo mode |
| S10 | `eval/` data | Rishika | none | CameraBench subset + own clips + human labels |
| S11 | `eval/score.py` | Siva (+Rishika charts) | gemini-decision-1 (verifier-vs-human only) | Baseline vs loop vs humans, calibration |
| S12 | `reshoot.py` | Prajesh | gemini-omni-1.2-flash | Edited scene → generated clip → verify against edited scene |
| S13 | deck, demo script | Rishika | none | Pitch, failure gallery, backup video |

## Key design decisions
- **Code generates checks, never the LLM.** Predictions come from geometry, so they are deterministic and auditable.
- **Abstain margins.** Only emit a check when the geometry is unambiguous (e.g. |bearing| > 6° for LEFT/RIGHT), so the verifier isn't asked borderline questions.
- **Monotone acceptance.** A corrected scene is kept only if the verified pass rate rises and at most 1 previously passing check breaks. This prevents the loop thrashing back and forth.
- **"Uncertain" never triggers correction.** Only clear fails (p on the wrong side of 0.3/0.7) go back to Argon.
- **Human labels are the only ground truth.** The verifier is a Gemini model; it may share Argon's blind spots.
- **Relative room units, not meters.** No metric-scale claims.
- **Clips are 5–15 s.** A single 30 s clip is used as a stress test only. Omni outputs are ≤15 s.
- **Cache everything.** The demo must run with Wi-Fi off.

## Milestones
- **M0 (12:30):** S00 done; mock `scene.json` committed; everyone builds against mocks.
- **M1 (3:30):** one real clip runs S01→S08 end to end.
- **M2 (5:00):** eval numbers on the test split locked. Go/no-go on S12 polish.
- **Freeze (6:30):** no new features. Pre-render demo reels.
