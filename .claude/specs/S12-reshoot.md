# S12: Re-shoot with Omni + conformance check

- **Owner:** Prajesh (Akash runs the 12:30 spike; Akash wires the UI button)
- **Status:** todo
- **Depends on:** S06, S09 (the spike has no dependencies)
- **Target:** spike 12:45; full 6:30
- **Models:** `MODEL_RESHOOT` = gemini-omni-1.2-flash (Interactions API); `MODEL_CHEAP` = gemini-4-hydrogen to write the shot description; `MODEL_VERIFY` for conformance

## Goal
From an EDITED map, generate a new shot with Omni, then verify whether the generated video actually follows the edit.

## Files
- `godshot/reshoot.py`
- `godshot/prompts/shot_description.md`
- `scripts/omni_spike.py`

## Spec
- **Spike (12:30, Akash, ≤ 15 min):** `scripts/omni_spike.py` takes 1 reference frame + "same room, the camera slowly arcs to the right around the person" → 5 s 360p. Record latency and whether it roughly follows; write the result in Notes. Go/no-go.
- `describe_shot(scene_edited) -> str` (Hydrogen): turn the camera track + motion segments + actor/object `visual_description`s into a concrete shot description (start framing, move, end framing, which objects are in which third, lens feel from hfov).
- `reshoot(clip, scene_orig, scene_edited, out, resolution="360p", duration="8s") -> Path`: ≤ 6 reference images (3 keyframes from the original + up to 3 actor/object crops via bounding box guesses or full frames); `response_format={"type":"video","duration":...,"resolution":...,"aspect_ratio":"16:9"}`; save mp4; cache by (refs + prompt + params). Label the output "AI-generated re-shoot".
- `conformance(generated_clip, scene_edited) -> {"score": k/n, "checks": [...]}`: run S04 `make_checks(scene_edited)` (times rescaled to the generated duration) + S05 `verify` on the generated frames. Also extract a scene from the generated clip (S02) and compare motion segments with the edit.
- Run generations in parallel in the background (Tier 3 limits); never generate live on stage. Pre-render 2–3 demo pairs by 6:30. Use `compose_compare` (S08).

## Acceptance criteria
- [ ] Spike result is logged by 12:45 (latency, instruction-following yes/no).
- [ ] One edited map → generated clip → conformance score shown in the UI.
- [ ] A human checks 5 generated clips against their edits; agreement with the verifier is reported in results.md.

## Notes / changelog
-
