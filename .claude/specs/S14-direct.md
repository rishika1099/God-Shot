# S14: Direct (scene analyst + director agents for re-shoots)

- **Owner:** Akash (edits to `godshot/reshoot.py` approved by its owner, Prajesh, 2026-11-21)
- **Status:** in review
- **Depends on:** S12 (re-shoot), S05 (Decisions `ask`), S03 (geometry), S01 (upload)
- **Models:** `MODEL_EXTRACT` (gemini-4-argon, `thinking_level=HIGH`) for the analyst, director and critic; `MODEL_CHEAP` (gemini-4-hydrogen) for the edit interpreter; `MODEL_VERIFY` (gemini-decision-1) for the preservation and fulfilment scores. Gemini only.

## Goal
A re-shoot must follow the EDITED map *and* keep what the original shot was about. Today the Omni prompt is purely
geometric (camera moves, colour legend, "X walks left to right"), so gestures, props, performance, lighting and
the dramatic intent are lost, and nothing measures that loss.

## Files
- `godshot/direct.py`
- `godshot/prompts/scene_brief.md`, `godshot/prompts/interpret.md`, `godshot/prompts/direct.md`, `godshot/prompts/critic.md`
- `godshot/reshoot.py` (opt-in hook only; default behaviour unchanged)
- `godshot/__main__.py` (`reshoot --direct/--preserve/--request/--critic/--w-preserve`)
- `tests/test_direct.py`

## Spec
1. **Scene analyst** (`analyze_scene`, Argon HIGH, watches the ORIGINAL clip): `SceneBrief`: logline, intent,
   timed beats per person (action, gaze/gesture, prop contact), performance (gait, pace, posture, emotion,
   wardrobe), environment, physics, ≤ 8 **must-preserve** items, each with a yes/no visual check. People are named
   by `visual_description`, never by id. Cached per clip.
2. **Scene diff** (`scene_diff`, pure geometry): per actor path length, mean speed and **speed while moving**
   (1 unit ≈ 1 m, as S12), gait, start/end, distance to the camera, nearest object at the end; added, removed and
   moved entities; camera moves before/after (S12 `shot_facts`) and `camera.changed`.
3. **Edit interpreter** (`interpret`, Hydrogen): map diff + optional free-text `request` → `ChangeSpec`: typed
   changes (blocking, camera, timing, performance, environment, event, framing; source map|request), every
   must-preserve item **locked or released** with a reason (`normalize_spec`: exactly one; released wins; default
   locked), ≤ 6 success checks for requested changes, assumptions.
4. **Director** (`direct`, Argon HIGH): brief + diff + spec → `Direction`: per-person adaptations (change →
   physical reasoning → behaviour) from a general physics checklist, beat re-timing, how each LOCKED element
   survives, conflicts + resolutions, `performance_prompt`, and `scene_changes_prompt` for requested non-body
   changes.
5. **Prompt** (`compose`): S12's geometric prompt first and verbatim, then sections in priority order: PERFORMANCE
   (with the "camera and positions win" rule), REQUESTED CHANGES, CORRECTIONS, MUST KEEP (locked only), WARDROBE,
   LIGHT (both skipped when the request changes them), SCENE. The lowest-priority sections are dropped to fit
   `MAX_PROMPT_CHARS = 3400`; the base is never cut.
6. **Scores** (Decisions `noul` on 3 frames): `preservation` = LOCKED items; `fulfilment` = success checks;
   ranking = (1−w)·conformance + w·mean(preservation, fulfilment), w = 0.4.
7. **Critic** (`critique`, Argon HIGH, watches the unlabelled take): accept | revise, with typed issues and 1–4
   corrections sentences. `critic_rounds` = N adds at most N corrected takes and stops at "accept".
8. **S12 hook** (opt-in; default unchanged): `reshoot(direction=Pack|(brief, Direction))`,
   `reshoot_best(direct, score_preservation, w_preserve, request, critic_rounds)`; `request`/`critic_rounds`
   imply `direct`. A failed Omni take is retried once with a compact prompt (`COMPACT_PROMPT_CHARS = 2800`).
   Artifacts go in `<out>/direct/` (`brief`, `diff`, `change_spec`, `direction`, `critique_<r>` .json).
   CLI: `godshot reshoot … --direct | --request "…" | --critic N | --preserve | --w-preserve`.

## Acceptance criteria
- [x] Offline tests: diff facts, prompt assembly (the geometry text is kept verbatim), the ranking formula, and
      the default S12 behaviour unchanged (all S12 tests still pass).
- [x] Live: one Tears of Steel clip + an edited map → brief, direction and a directed re-shoot, with both
      conformance and preservation reported (4 live runs; see Notes).
- [x] Directed vs undirected on the same edit: preservation is reported for both (a single clip is a signal, not
      a result). See Notes: 0.85 → 0.91.

## Notes / changelog
- 2026-11-21 (Akash), v1: analyst + diff + director + preservation + an opt-in S12 hook (approved by Prajesh).
- **First live A/B** (`clips/tos_158s.mp4`, Tears of Steel CC-BY; S02 map; edit = the man walks 8.2 m to the
  glowing archway in 6 s instead of standing by the rope; 1 take each):

  | | plain S12 | directed |
  |---|---|---|
  | conformance (map) | 0.87 (48/55) | **0.90** (43/48) |
  | preservation (mean P(yes)) | 0.85 | **0.91** |
  | "rubbing the left shoulder" | 0.54 | **0.88** |

  **By eye:** the plain take drops the rope landing and the shoulder injury (he just walks away); the directed
  take lands holding the rope, grips his shoulder at 0:01–0:02, turns and trudges to the archway. Single-frame
  checks *under-rate* the gap (the plain take scored 0.83 on "landing at the rope" because the rope is in shot),
  which is why the critic watches the whole video. One clip, one take each: a signal, not a result.
- **Director quality:** it found that the landing, release and turn must compress into the first 2 s, overlapped
  the shoulder rub with the start of the walk, played 1.36 m/s as a "laboured, heavy-footed trudge" to keep the
  weariness, and flagged the conflict with the locked "slow walk" item. That conflict is what motivated v2's
  lock/release.
- **Omni flake:** the first directed call failed with a generic `INVALID_ARGUMENT`; the *identical* prompt
  succeeded on retry, and a padded 4.6k-char prompt also succeeded (so it isn't length). `reshoot_best` now
  retries a failed take once. The prompt is budgeted to `MAX_PROMPT_CHARS = 4400` (lowest-priority sections dropped
  first; the S12 base is never cut).
- **v2 (generalisation):**
  - **Edit interpreter** (`interpret`, Hydrogen): map diff + optional free-text `request` → `ChangeSpec`: typed
    changes (blocking, camera, timing, performance, environment, event, framing; source map|request), every
    must-preserve item **locked or released** by index with a reason (`normalize_spec` enforces exactly one;
    released wins; default locked), success checks for requested changes, assumptions.
  - **Director** gets the spec plus a general physics checklist (speed/gait from `moving_speed_mps`,
    acceleration, turns, contact/support, cause→effect, secondary motion, environment response, non-human
    movers) and writes `scene_changes_prompt` for requested non-body changes.
  - **Scoring:** preservation counts LOCKED items only; **fulfilment** = success checks; rank by
    (1−w)·conformance + w·mean(preservation, fulfilment), w = 0.4.
  - **Critic** (`critique`, Argon watching the unlabelled take): accept | revise, with typed issues (request,
    action, identity/wardrobe, physics, blocking/camera, environment, artifact) and 1–4 corrections sentences.
    `critic_rounds` = N adds at most N corrected takes, stops at "accept"; `critique_<r>.json`.
  - The diff now also reports removed objects, `camera.changed`, and speed while moving.
  - `request` or `critic_rounds` imply `direct`. Defaults are unchanged: all 20 S12 tests pass.
- Tests: `tests/test_direct.py`, 28 tests, offline (fake Gemini + fake Decisions + real tiny ffmpeg videos).
- **Live demo runs** (`tos_158s`, S02 map; 1–2 takes + 1 critic round each; frame-check scores, so treat them as a
  demo, not a measurement):

  | edit | map | request | released | conformance | preservation | fulfilment |
  |---|---|---|---|---|---|---|
  | A | walk 8 m to the archway | limp + red emergency lights | none | 0.95 | 0.90 (6/6) | ❌ 0.48 (0/2) |
  | B | walk to the booth + camera push-in 2.5 m | strong, unhurt + golden sunset | **shoulder rub** | **0.98** | 0.89 (5/5) | ✅ **0.88 (2/2)** |
  | C | camera orbits 45° right + 3 m weary walk | dust on landing + glance up the rope | none | ❌ 0.59 | **0.92** (6/6) | ~0.57 (2/2) |

  - **The critic loop works:** on B, take 1 had no sunset; the critic said so, and the corrected take 2 has golden
    shafts from the high windows (fulfilment 0.64 → 0.88). It also caught real physics glitches (a second rope
    dropping from nowhere, boots passing through a panel) and the missing orbit on C.
  - **Omni limits seen:** (1) environment changes far from the reference frames (red light in a blue-grey hall)
    were not rendered, even after correction; warm light near the archway's own glow was. (2) An orbit on this
    tight, close-framed shot was ignored in all 3 takes (Prajesh's spike: arcs work on wide shots). Next: try
    references without full frames for environment requests, and orbits on a wide shot (`tos_258s`).
  - Omni's generic `INVALID_ARGUMENT` hit prompts of ~4.2k+ chars intermittently (both attempts on one 4.2k
    prompt); all prompts ≤ ~3.2k passed → budget 3.4k, compact retry 2.8k.
