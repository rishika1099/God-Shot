# S13: Pitch (deck, demo script, failure gallery, backup video)

- **Owner:** Rishika (Akash builds the failure gallery stills; everyone rehearses)
- **Status:** todo
- **Depends on:** S11 (numbers), S08/S12 (pre-rendered reels)
- **Models:** none

## Goal
A 3-minute demo that proves the claim with our own numbers, and survives bad Wi-Fi.

## Files
- `assets/pitch/deck.pdf` (or a link in README), `assets/pitch/demo_script.md`, `assets/pitch/backup.mp4` (gitignored if large; a link in README)
- `assets/img/failure_gallery.png`

## Demo script (3:00)
| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | Describe a hand-made breakdown reel (no copyrighted footage) → our reel | "Creators hand-animate these overhead breakdowns. God-Shot makes one from a single shot and tells you where it's unsure." |
| 0:20–1:10 | Cached run on a Tears of Steel clip: JSON → checks going green/red → round 2 fixes the door side + pan direction → reel with "?" badges | "Gemini 4 proposes the geometry. Our code turns it into dozens of yes/no predictions. Gemini Decision 1 checks each one against the frames. Failures go back for correction." |
| 1:10–1:50 | Baseline vs loop bars with CIs; CameraBench result; verifier accuracy; reliability diagram; selective accuracy | "On held-out clips, including an expert-labeled benchmark we didn't write: [baseline]% → [loop]%. Items flagged low-confidence are wrong [k]× more often." |
| 1:50–2:30 | Drag the camera on the map → pre-rendered Omni re-shoot → conformance 7/9 | "The same checker works on footage we generate, so you can see whether the video model followed your camera direction." |
| 2:30–2:50 | Failure gallery (whip pan, crowd, low light, 30 s long take) | "Here's where Gemini 4 breaks, and what we changed after seeing it." |
| 2:50–3:00 | Cost/latency line + repo | "About [$] and [s] per clip. Code, labels and credits are in the repo." |

## Spec
- Use ONLY numbers from `eval/results.md`; write the dev/test split and freeze time on the slide.
- Answer the Element 119 questions explicitly: hardest input; what raw video gives that text loses; how far we pushed before it broke and what we changed.
- Prepare Q&A: "Isn't Gemini grading Gemini?" (headline accuracy uses human labels only; verifier-vs-human agreement is reported); "Why not fine-tune?" (inference-time reliability with the models as shipped; keeps the eval clean); "Why not SfM?" (no semantics, needs a GPU; CameraBench shows SfM misses semantic primitives).
- A 3-minute backup screen recording of the cached demo.

## Acceptance criteria
- [ ] Two full rehearsals under 3:00.
- [ ] The demo runs in offline cached mode.
- [ ] README has final numbers, credits, a team list and a "built today" note; repo is public; submission done.

## Notes / changelog
-
