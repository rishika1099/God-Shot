# God-Shot — 3-Minute Demo Script, Deck Outline & Q&A Prep (S13)

- **Owner:** Rishika (Akash builds `assets/img/failure_gallery.png`; everyone rehearses ×2 under 3:00)
- **Track:** Element 119 (Gemini 4 NYC Hackathon, Nov 21, 2026)
- **Hard rule:** Replace every `[TBD]` **ONLY** with locked numbers from `eval/results.md` after the ≥ 4:00 PM freeze. Never invent or extrapolate metrics. Run the live demo in **offline cached mode** (`GODSHOT_OFFLINE=1`).

---

## 1. Timed 3:00 Demo Script

| Time | On Screen (Offline Cached UI / Slides) | Spoken Script |
|---|---|---|
| **0:00–0:20** *(20 s)* | Title card → describe hand-animated cinematography breakdown reels (no copyrighted film footage) → play our 9:16 God-Shot reel (`clip` on top, animated overhead blocking map below) | *"Film breakdown creators spend hours hand-animating top-down blocking diagrams to explain how a shot works. God-Shot turns a single continuous shot into a verified overhead map automatically — and flags every element it still doesn't trust."* |
| **0:20–1:10** *(50 s)* | Cached run on an open-licensed *Tears of Steel* shot: Round 0 Argon `scene_r0.json` → code-generated geometric checks turning green/red via Decisions API → Round 2 fixes the door side + pan direction on the shared question bank → final 9:16 reel with `"?"` low-confidence badges | *"First, Gemini 4 Argon watches the raw clip and proposes a 2D blocking map — walls, props, actors, and camera trajectory. Next, our deterministic geometry code turns that map into dozens of falsifiable yes/no and choice predictions — like whether the door should appear in the left half at 0:03. Gemini Decision 1 checks every prediction against the real frames and returns calibrated probabilities. Failed checks go back to Argon for targeted correction, and a fix is kept only if it improves score on a shared, non-shrinking question bank without introducing regressions. Anything still failing gets a `?` badge on the map."* |
| **1:10–1:50** *(40 s)* | Slide with `eval/figs/accuracy_by_type.png`, `accuracy_by_group.png` (CameraBench + own clips), `calibration.png` (reliability diagram, ECE/Brier), and `selective_accuracy.png`. Footer shows `dev` (n=5) / `test` (n=`[TBD]`) split + freeze time `[TBD]`. | *"We froze prompts and thresholds at `[TBD]` before touching the held-out test split, and every headline metric compares scene geometry against human labels — including 40 clips from CameraBench, an expert-labeled benchmark we didn't write. One Argon call gets `[TBD]%`; our verify-and-correct loop raises that to `[TBD]%` (95% CI `[TBD]`, paired sign test p=`[TBD]`). And the confidence signal works: elements flagged low-confidence are wrong `[TBD]×` more often."* |
| **1:50–2:30** *(40 s)* | Interactive map editor: drag the camera path / flip the move → instant map re-render → play pre-rendered Gemini Omni re-shoot → Decisions conformance scorecard (`[TBD]/[TBD]` intent checks pass) | *"Because the map is structured and editable, you can drag the camera path or move an actor and immediately re-render the diagram — or re-shoot the scene with Gemini Omni. Even better, we run the exact same geometric checker on the generated video to measure whether Omni actually followed your camera direction: `[TBD]` out of `[TBD]` intent checks passed."* |
| **2:30–2:50** *(20 s)* | 4-panel failure gallery (`assets/img/failure_gallery.png`: whip pan, crowd, low light, 30 s long take) | *"To push Gemini 4 to its breaking point, we collected stress tests filmed by non-team participants: whip pans, crowds, low light, and a 30-second long take. Here's where it broke — `[TBD: specific failure mode]` — and what we changed after seeing it: `[TBD: specific engineering fix, e.g. shared question bank / higher fps / abstain margins]`."* |
| **2:50–3:00** *(10 s)* | Closing slide: cost/latency line (`$[TBD]` and `[TBD] s` per clip), public GitHub repo QR/URL, open licenses & credits | *"Total runtime is `[TBD]` seconds and about `$[TBD]` per clip. All code, human labels, consent protocol, and open-licensed credits are in our public repo."* |

---

## 2. Slide-by-Slide Deck Outline (`assets/pitch/deck.pdf`)

1. **Slide 1 — Title & Hook:** God-Shot · Turn one continuous shot into a verified overhead blocking map, then edit & re-shoot it. Team: Rishika, Prajesh, Akash, Siva (Built Nov 21, 2026).
2. **Slide 2 — System Architecture:** `clip → Argon (baseline) → deterministic geometry checks → Decisions API (calibrated raw probabilities) → Argon correction loop (shared question bank, keep-if-better) → 9:16 reel + editable map + Omni re-shoot`.
3. **Slide 3 — Why a Shared Question Bank Matters:** Shows the reward-hacking modes we caught and prevented (moving wrong objects into abstain margins, deleting entities, rewriting descriptions, flipping sides to make old checks disappear).
4. **Slide 4 — Held-Out Evaluation (`eval/results.md`):**
   - Split: 5 `dev` clips; `[TBD]` own `test` clips + 40 CameraBench clips. Prompts/thresholds frozen at `[TBD]`.
   - `accuracy_by_type.png` + `accuracy_by_group.png` (CameraBench vs Own Clips).
   - Honest reporting: loop hurt `[TBD]%` of clips; total regressions = `[TBD]`.
5. **Slide 5 — Calibration & Selective Accuracy ("It Knows When It's Wrong"):**
   - `calibration.png` (5-bin reliability diagram, ECE=`[TBD]`, Brier=`[TBD]`, mirrored-control yes-bias=`[TBD]`, verifier-vs-human=`[TBD]%`).
   - `selective_accuracy.png` (high-confidence `[TBD]%` vs flagged `?` `[TBD]%`).
6. **Slide 6 — Edit & Re-Shoot Conformance (Gemini Omni):** Before/after blocking map + Omni re-shoot conformance (`[TBD]%` intent checks passed; `[TBD]/5` human-verified).
7. **Slide 7 — Failure Gallery & Element 119 Stress Tests:** `assets/img/failure_gallery.png` (whip pan, crowd, low light, 30 s long take) + what broke & what we changed.
8. **Slide 8 — Cost, Latency & Open Repo:** Median `[TBD] s/clip`, `$[TBD]/clip`, open licenses (`eval/CREDITS.md`), public repo URL.

---

## 3. Explicit Element 119 Track Answers

1. **What is the hardest input, and does it hold up?**
   - Our stress suite from non-team participants: **whip pan**, **dense crowd**, **low light**, and a **30 s continuous long take** (plus domain shift to Blender animated shorts *Sintel*/*Spring*/*Charge*).
   - *(Fill exact survival/break metrics from `eval/results.md` and `eval/failures.md` after test run.)*
2. **What does raw video give us that text loses?**
   - Continuous parallax, subtle camera translation (`dolly` vs `truck` vs `arc`), frame-accurate relative left/right/depth ordering across time, and visual verification against exact frame timestamps. Text captions collapse geometry and flip left/right or pan/truck directions; passing raw video (`fps=4`) + high-res failing frames lets Decisions ground spatial claims directly in pixels.
3. **How far did we push before it broke, and what did we change?**
   - **Caught during design/dev:** (a) Naive per-round scoring let corrections "cheat" by deleting failing entities or nudging them into abstain margins → built the **shared accumulating question bank** (`schema.qkey`, `predict()`, `freeze_identity()`, reject if `regressions > 1`); (b) CameraBench `arc-CW`/`arc-CCW` taxonomy verification caught an inverted left/right arc convention (`arc-CW` = `truck-left + pan-right` → `arc_left`); (c) *(add concrete dev-clip failure & fix found during M1/M2!)*.

---

## 4. Judge Q&A Prep

- **"Isn't Gemini grading Gemini?"**
  - *No.* Every headline accuracy number compares the reconstructed `scene.json` against **human labels** (`label.v1`) written before seeing model outputs, plus 40 expert-labeled CameraBench clips. We separately report Decisions-vs-human agreement (`[TBD]%`), calibration (ECE/Brier), and mirrored-control yes-bias (`[TBD]`) so the verifier's own error rate is completely transparent.
- **"Why not fine-tune?"**
  - We wanted inference-time geometric reliability with the shipped Gemini 4 + Decisions API models — zero task-specific training data, works zero-shot on unseen rooms/actors, and keeps the evaluation completely clean against held-out benchmarks.
- **"Why not classical SfM / SLAM?"**
  - Classical SfM requires heavy GPU compute, struggles with moving actors, low-texture walls, and handheld drift, and produces unlabelled point clouds with no semantic entities ("the woman in the grey jacket", "the green wooden door"). Even on pure camera motion, the CameraBench paper shows SfM misses semantic camera primitives that VLMs capture.
- **"Why use the Decisions API instead of prompting Gemini 4 again?"**
  - Decisions returns calibrated probabilities (`noul` / `choice`) without text generation, batches dozens of frame-level checks in parallel in seconds, and lets us separate raw visual observations from scene-relative geometry verdicts.
- **"Are all clips properly licensed?"**
  - Yes — zero copyrighted film footage. Every clip is CameraBench (CC BY 4.0, numbers only — never shown in demo), Blender Open Movies (*Tears of Steel* CC BY 3.0, *Sintel*/*Spring*/*Charge* CC BY), Pexels (free license), or venue clips filmed by non-team volunteers with signed consent forms (`eval/CREDITS.md`).

---

## 5. Pre-Demo & Submission Checklist (Freeze 6:30 · Submit 7:50)

- [ ] Replace every `[TBD]` above with locked numbers from `eval/results.md` (write split & freeze time on slide).
- [ ] Akash delivers `assets/img/failure_gallery.png` (4 panels from `eval/failures.md`).
- [ ] Export slides to `assets/pitch/deck.pdf` (or link in `README.md`).
- [ ] Test full demo with `GODSHOT_OFFLINE=1` (zero network calls).
- [ ] Record 3-minute backup video → `assets/pitch/backup.mp4`.
- [ ] Complete **2 full timed rehearsals under 3:00**.
- [ ] Finalize `README.md`: locked numbers, credits, team list, "built today" note; confirm repo is public; submit by **7:50 PM**.
