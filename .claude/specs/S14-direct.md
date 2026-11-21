# S14: Direct (scene analyst + director agents for re-shoots)

- **Owner:** Akash (edits to `godshot/reshoot.py` approved by its owner, Prajesh, 2026-11-21)
- **Status:** in progress
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
1. **Scene analyst:** `analyze_scene(clip, scene_orig) -> SceneBrief`. Argon watches the ORIGINAL clip (Files API
   video part) with the original map as context. Output (JSON schema enforced): logline, intent, timed action
   beats per person (action, gaze/gesture, prop contact), performance per person (gait, pace, posture, emotion,
   wardrobe), environment (light, palette, atmosphere), notable physics, and ≤ 8 **must-preserve** items, each
   with a yes/no visual check that is TRUE when the element is preserved. People are named by their
   `visual_description`, never by id. Cached.
2. **Scene diff (pure code):** `scene_diff(orig, edited)`: per actor, path length, mean speed (1 unit ≈ 1 m, as
   S12), implied gait, net direction, start/end shift, change in distance to the camera, nearest object at the
   end; moved, added and removed entities; camera moves before/after (S12 `shot_facts`). Facts only, no judgement.
3. **Director:** `direct(brief, scene_orig, scene_edited) -> Direction`. Argon, HIGH thinking, given the brief and
   the diff. It deliberates how each person's behaviour and physics must change to look natural under the edit
   (speed, gait, lean, weight shift, timing, contact, gaze relative to the new camera) while keeping the intent
   and every must-preserve item. Output: change summary, per-person adaptations (change → physical reasoning →
   adjusted behaviour), beat re-timing, how each must-preserve item survives, conflicts + resolutions, and one
   concise performance paragraph for the video model. Cached.
4. **Directed prompt:** `directed_prompt(base, brief, direction)`. S12's geometric prompt stays first and
   authoritative; then a PERFORMANCE section (the director's paragraph), a MUST KEEP list, and the rule "if
   anything below conflicts with the camera or positions above, the camera and positions win".
5. **Preservation score:** `preservation(video, brief) -> {score, items}`. Each must-preserve check is asked
   (Decisions `noul`) on 3 frames of the generated clip. score = mean P(yes) over items; an item is kept when
   P(yes) ≥ 0.5.
6. **Hook in S12:** `reshoot(..., direction=None)` and `reshoot_best(..., direct=False, w_preserve=0.4)`. With
   `direct=True`, the brief and direction are computed once, every take uses the directed prompt, and takes are
   ranked by `(1 - w)·conformance + w·preservation`. Artifacts go in `<out>/direct/` (`brief.json`, `diff.json`,
   `direction.json`).

## Acceptance criteria
- [x] Offline tests: diff facts, prompt assembly (the geometry text is kept verbatim), the ranking formula, and
      the default S12 behaviour unchanged (all S12 tests still pass).
- [ ] Live: one Tears of Steel clip + an edited map → brief, direction and a directed re-shoot, with both
      conformance and preservation reported.
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
