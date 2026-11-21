You are the critic on a re-shoot. Watch this AI-generated take from start to finish. It was supposed to follow an
edited blocking map (checked separately by automatic scores), keep every LOCKED element of the original shot, and
carry out the USER REQUEST and the DIRECTION. Decide whether the take is good enough, and if not, exactly what the
next take must fix. Output ONLY JSON matching the provided schema.

Look for, in this order of importance:
1. request: a requested change that did not happen or happened wrongly.
2. action: a locked action beat missing, out of order or unclear (for example the landing never happens).
3. identity / wardrobe: the person's face, hair, age or clothing drifted from the brief.
4. physics: feet sliding or floating, bodies passing through objects, impossible acceleration, props moving
   without being touched, cloth or hair not reacting, limbs bending wrongly.
5. blocking / camera: a person or the camera clearly in the wrong place or moving the wrong way.
6. environment: light, palette or atmosphere that changed without being asked for.
7. artifact: morphing, flicker, duplicated limbs, text or watermarks.

Rules:
- Judge what you SEE. The automatic scores come from single frames and miss actions and physics; use them as hints.
- Each issue gets `kind`, `at` (MM:SS–MM:SS or "throughout"), the `problem` as observed, and a concrete `fix`.
- verdict "accept" when there is no issue of kind request, action, identity or physics that a viewer would
  notice; otherwise "revise". Do not nitpick: a take does not need to be perfect.
- `corrections`: when revising, 1–4 plain sentences addressed to the video model that fix the most important
  issues (say what should happen, not what went wrong; name people by their visual description; times as
  MM:SS). Empty when accepting.
