# S14: Direct (scene analyst + director agents for re-shoots)

- **Owner:** Akash (edits to `godshot/reshoot.py` approved by its owner, Prajesh, 2026-11-21)
- **Status:** in progress
- **Depends on:** S12 (re-shoot), S05 (Decisions `ask`), S03 (geometry), S01 (upload)
- **Models:** `MODEL_EXTRACT` (gemini-4-argon) for both agents, `thinking_level=HIGH`; `MODEL_VERIFY` (gemini-decision-1) for the preservation score. Gemini only.

## Goal
A re-shoot must follow the EDITED map *and* keep what the original shot was about. Today the Omni prompt is purely
geometric (camera moves, colour legend, "X walks left to right"), so gestures, props, performance, lighting and
the dramatic intent are lost, and nothing measures that loss.

## Files
- `godshot/direct.py`
- `godshot/prompts/scene_brief.md`, `godshot/prompts/direct.md`
- `godshot/reshoot.py` (opt-in hook only; default behaviour unchanged)
- `godshot/__main__.py` (`reshoot --direct` flag)
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
- [ ] Offline tests: diff facts, prompt assembly (the geometry text is kept verbatim), the ranking formula, and
      the default S12 behaviour unchanged (all S12 tests still pass).
- [ ] Live: one Tears of Steel clip + an edited map → brief, direction and a directed re-shoot, with both
      conformance and preservation reported.
- [ ] Directed vs undirected on the same edit: preservation is reported for both (a single clip is a signal, not
      a result).

## Notes / changelog
-
