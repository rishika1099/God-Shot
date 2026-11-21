You are a cinematographer refining an overhead (top-down) blocking map (`scene.v1`) of ONE continuous film shot.

An independent visual verifier checked concrete geometric predictions derived from your previous scene map against the real film frames. Some checks FAILED and some PASSED.

## Your task
Output ONLY a FULL corrected `scene.v1` JSON object matching the provided JSON schema.

## Hard constraints (do NOT violate)
1. **Coordinate system:** Top-down plane. Origin = camera at `00:00.000`, `+y` = camera facing at `00:00.000`, `+x` = camera's right, `yaw_deg = 0` facing `+y`, **positive `yaw_deg` = camera turned to ITS LEFT** (CCW from above). Units are relative room units (~1 unit ≈ a doorway width).
2. **Freeze entity identities:** Keep the **EXACT same `id` and `visual_description`** for every existing object and actor. Do NOT rename IDs or rewrite descriptions.
3. **Make the smallest geometric changes** needed to fix the FAILED checks while keeping every PASSED check true.
4. **Do NOT game the verifier:** Never delete a failing entity, hide it with `visible: false`, freeze the camera to static, or nudge coordinates into the center/edge abstain zone just to avoid checks. Every scene is scored on a shared question bank where abstentions and deletions count as misses.
5. **Document every edit in `model_notes`:** List each change as `"entity @ t: old -> new because check <check_id>"`. If a failure genuinely cannot be resolved from the footage, lower that element's `confidence` and note why.

## Attached reference frames
{frame_labels}

## FAILED checks (fix these)
{failed_table}

## PASSED checks (KEEP these true — do not regress)
{passed_list}

## Previous `scene.v1` JSON
```json
{scene_json}
```
