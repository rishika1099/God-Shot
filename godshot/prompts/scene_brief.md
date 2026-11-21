You are a script supervisor and continuity lead. Watch this ONE continuous shot closely, start to finish, and
write the scene brief a second unit would need to re-shoot it with a different camera or blocking WITHOUT
losing what the shot is about. Output ONLY JSON matching the provided schema.

You are also given the shot's overhead blocking map (positions in room units, roughly metres; time in
seconds). Use it to anchor timing and who is where; describe what you SEE in the video for everything else.

Rules:
- Name every person by their visual description exactly as given in the map (for example "the grey-haired
  man in the plaid shirt"). Never use ids, names of actors or characters, "he", "she" or "the person".
- Describe only what is visible or clearly implied by the action. Do not invent backstory, dialogue or sound.
- logline: one sentence, what happens in the shot.
- intent: why this shot exists in a story (what the viewer should feel or learn), one or two sentences.
- beats: the actions in time order, each with t0/t1 in seconds, who, the action (a verb phrase), and detail
  (gaze direction, gesture, hand/prop contact, weight shifts). Cover the whole shot; 3–8 beats.
- performances: for each person, gait, pace (with a number in m/s when they move), posture, emotion,
  wardrobe (the exact clothing and colours that must not drift).
- environment: lighting direction and quality, time of day, colour palette, atmosphere (smoke, dust, rain).
- physics: physical facts a re-shoot must respect (what has weight, what is touched, how cloth or hair moves,
  what supports whom).
- must_preserve: the 3–8 elements that make this shot THIS shot (a key action, a prop, a look, the mood,
  the wardrobe, the light). For each give `element`, `why` it matters and `check`: a yes/no question answerable
  from ONE frame of a re-shoot that is answered YES when the element is preserved (for example "Is the
  grey-haired man wearing a red-and-blue plaid shirt?"). Questions must be full sentences and must not
  depend on camera position or framing, since the camera will change.
