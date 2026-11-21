# God-Shot

**Turn one continuous film shot into a verified overhead blocking map, then edit the map and re-shoot it.**

Built at the Gemini 4 NYC Hackathon (Cerebral Valley × Google DeepMind), Nov 21, 2026. Track: **Element 119**.

## What it does

1. **Upload a single shot** (5–15 s, no cuts).
2. **Gemini 4 Argon** watches it and writes a scene map: room, furniture, actors and camera path (`scene.json`).
3. **Our code** turns that map into dozens of geometric yes/no predictions ("the door should be in the left half at 0:03").
4. **Gemini Decision 1** (Decisions API) checks every prediction against the real frames and returns calibrated probabilities.
5. **Argon corrects** whatever failed. A correction is kept only if it improves the pass rate. Up to 3 rounds.
6. **We render a 9:16 reel**: the clip on top and the animated top-down map below. Parts it still doesn't trust get "?" badges.
7. **Edit the map** (move the camera, change the move, move an actor) → re-render instantly, or **re-shoot** with Gemini Omni and verify whether the generated video followed your edit.

## Core claim (what we measure)

> A single Argon call gets camera motion and layout wrong X% of the time. Our verify-and-fix loop lowers that to Y% on held-out clips scored by humans, and the elements it flags low-confidence really are wrong more often.

Numbers are filled in only from our own held-out runs (see `eval/results.md`).

## Architecture

```
clip ─► ingest (Files API + ffmpeg frames)
     ─► extract (gemini-4-argon)            → scene.json   (round 0 = BASELINE)
     ─► checks  (our geometry code)          → check.v1 list
     ─► verify  (gemini-decision-1)          → p(yes) per check
     ─► correct (gemini-4-argon, ≤3 rounds, keep-if-better)
     ─► render  (matplotlib/Pillow + ffmpeg) → reel.mp4 (clip over map)
edit map ─► re-render  |  re-shoot (gemini-omni-1.2-flash) ─► verify vs EDITED map
```

Details: [`.claude/docs/architecture.md`](.claude/docs/architecture.md)

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # paste your GEMINI_API_KEY
brew install ffmpeg         # or apt install ffmpeg
```

## Run (as specs land)

```bash
python -m godshot run clips/demo.mp4 -o out/demo      # full loop + reel
python -m godshot baseline clips/demo.mp4 -o out/demo # single Argon call
python -m godshot eval --system full|baseline         # eval/results.md
python -m godshot web                                 # local UI + cached demo mode
```

## How we build: spec-driven

Every piece of work is a spec in [`.claude/specs/`](.claude/specs/). Each has an owner, inputs/outputs, the models to use, and acceptance criteria. Tell your agent:

> "Read CLAUDE.md, then implement spec S0X. Only touch the files that spec lists."

| Owner | Area | Specs |
|---|---|---|
| **Prajesh** | Gemini extraction, correction loop, Omni re-shoot | S00, S01, S02, S06, S12 |
| **Siva** | Geometry, checks, Decisions verifier, scoring | S03, S04, S05, S11 |
| **Akash** | Map renderer, reel, web UI, map editor | S07, S08, S09 |
| **Rishika** | Eval data + labels, metrics, charts, pitch | S10, S13 |

Status board: [`.claude/specs/00-index.md`](.claude/specs/00-index.md)

## Data and licenses

- Eval: a CameraBench subset (expert camera-motion labels), *Tears of Steel* / *Sintel* / *Spring* / *Charge* (Blender open movies, CC BY), Pexels clips, and venue clips filmed by consenting non-team members.
- Credits for every clip are listed in `eval/CREDITS.md` and on the reel end card.
- No copyrighted film footage in the dataset or demo.

## Team

Rishika · Prajesh · Akash · Siva
