# Evaluation design

## Claim to prove
Baseline (one Argon call = `scene_r0.json`) vs. loop (`scene_final.json`), both scored against **human labels**, on clips we did not tune on.

## Data (owner: Rishika, S10)
| Set | Size | Measures | Labels from |
|---|---|---|---|
| CameraBench subset (HF `syCen/CameraBench`) | 30–50 clips | Camera motion: pan, dolly, truck, arc, static | External experts (map their tags to our enums) |
| Tears of Steel / Sintel / Spring / Charge + Pexels | 10–15 clips | Layout: SIDE, A_LEFT_OF_B, CLOSER, TOWARD + motion | Team, written BEFORE seeing output |
| Venue clips filmed by non-team people | 8–10 clips | Messy real use; optional camera end position on a taped grid | Team + tape measurements |
| Stress test | 1–3 clips | A 30 s long take, whip pan, crowd, low light | Team |

Check the CameraBench license before showing any of its clips; use them for numbers only.

## Splits
- `dev`: 5 clips (mixed sources) for prompts, thresholds and abstain margins.
- `test`: everything else. **Freeze prompts and thresholds at ~4:00 PM before running test.** Say so on the slide.

## Metrics (S11)
1. **Scene accuracy vs humans, per label type:** run each human label through `geometry.py` on `scene_r0` and on `scene_final`. Grouped bars with 95% bootstrap CIs (resample by clip) + a per-clip paired sign test.
2. **Verifier accuracy vs humans:** ask Decisions the human-label questions directly. Accuracy, precision/recall on "fail", yes-bias from mirrored controls.
3. **Calibration:** reliability diagram (5 bins), ECE, Brier for `noul` vs human truth.
4. **Selective accuracy:** accuracy of high-confidence vs `low_confidence` elements. Proves "it knows when it's wrong."
5. **Loop behavior:** rounds-to-converge histogram, regression count, share of clips where the loop HURT (report honestly).
6. **Cost + latency per clip.**
7. **Failure gallery:** 4 panels (whip pan, crowd, low light, long take) with reel stills and the failing checks.
8. **Re-shoot conformance (S12):** % of edited-map intent checks that pass on Omni output; human-check 5 of them.
9. Optional ablations: Argon vs Hydrogen; fps 1 vs 4; 15 s vs 30 s.

## Labeling protocol
- 6–10 labels per clip; times rounded to 0.5 s.
- Double-label 25% of clips → report inter-annotator agreement.
- Everyone labels for 20 minutes at ~1:30 PM while the baseline runs.
