# God-Shot: agent instructions

You are working in a 4-person hackathon repo. Read this file fully before writing code.

## Project in one paragraph
God-Shot turns one continuous video shot into an overhead blocking map (`scene.json`), verifies the map against the real frames with the Gemini Decisions API, corrects it with Gemini 4 Argon, and renders a 9:16 reel (clip on top, animated map below). Users can edit the map and re-render or re-shoot it with Gemini Omni. Full context: `.claude/docs/architecture.md`.

## How to work (spec-driven)
1. Your human will name a spec, e.g. "implement S05". Open `.claude/specs/S05-*.md`.
2. Read the spec's **Depends on** specs and `.claude/docs/contracts.md` before coding.
3. Implement ONLY that spec. Touch only the files the spec lists under **Files**. If you need a change to a shared contract, STOP and tell your human; contracts are frozen (see below).
4. Meet every **Acceptance criteria** item, and add or extend the tests the spec names.
5. When done, update the spec: set `Status: done`, fill in the **Notes / changelog** section (what you built, deviations, gotchas), and update the row in `.claude/specs/00-index.md`.
6. Commit on your branch and open a PR (see **Git workflow** below). Don't merge it yourself.
7. If blocked more than 20 minutes, write the blocker in the spec's Notes and tell your human.

## Git workflow (own branches, group-approved PRs)
- **Never commit or push to `main`.** `main` only changes through PRs that the group approved.
- **Work only on your owner's branches.** One branch per spec, named `<owner>/<spec>-<slug>` (lowercase), e.g. `prajesh/s00-foundation`, `siva/s05-verify`, `akash/s02-extract`, `rishika/s06-correct-loop`. The owner is the one in `.claude/specs/00-index.md`. Never commit to another person's branch.
- **Build on top of your own branches.** Start a spec from the latest `main`. If it depends on one of YOUR specs that isn't merged yet, branch from that spec's branch instead (a stacked branch), e.g. `prajesh/s01-ingest` from `prajesh/s00-foundation`. If you need a teammate's unmerged work, build against the mocks in `mocks/`; don't branch from their branch.
- **Before opening the PR,** run `ruff check` + `pytest` (both must pass), and update the spec Notes and the index row in the same branch.
- **One PR per spec.** The PR title is `S05: Verify (Decisions API client)`. The PR body links the spec and covers: what was built, the acceptance-criteria checklist, deviations, any contract change (with its contracts.md changelog line), and how to test. A stacked PR names its base PR. Open it against `main`, or against your own base branch while the base PR is still open.
- **PRs are approved together, as a group.** Review happens at group syncs (each milestone in `.claude/specs/00-index.md`, or whenever all four agree to meet). All four people look at the open PRs together; a PR merges only after the group approves it. Merge base PRs before the PRs stacked on them. Agents never approve, merge, force-push to `main` or close someone else's PR; only humans do, at the group review.
- **After a merge,** rebase your other open branches onto the new `main` (`git fetch && git rebase origin/main`). If a rebase conflict touches someone else's files, stop and tell your human.
- Never commit `.env`, `.cache/`, `out/`, `frames/` or video files (see `.gitignore`).

## Hard rules
- **Contracts are frozen** once S00 is done: `scene.v1`, `check.v1`, `label.v1` in `godshot/schema.py` and `.claude/docs/contracts.md`. Additive optional fields are OK *only* with a note in contracts.md; never rename or remove fields.
- **Models come from `godshot/config.py`**, which reads `.env`. Never hardcode a model ID or API key anywhere else. See `.claude/docs/models.md` for which model does what.
- **API keys live only in `.env`** (gitignored). Never print, log or commit them.
- **Verify new APIs against current docs before coding.** Gemini 4, the Decisions API and Omni are new; check field names in the official docs or by listing models via the SDK. Mark anything unverified with `# VERIFY:` comments.
- **Cache every API response** on disk under `.cache/` keyed by `sha256(clip_bytes + model + prompt + params)`. Re-runs and the demo must work offline from cache.
- **The verifier never grades final accuracy.** Headline metrics compare scene JSON against HUMAN labels only (`label.v1`).
- **Freeze prompts and thresholds before the test split runs.** Tune only on clips with `split: dev`.
- **No Streamlit** (banned by hackathon rules). The UI is FastAPI + plain HTML/JS.
- **No copyrighted film footage** in `clips/`, `eval/` or the demo. Open-licensed sources only; credit them in `eval/CREDITS.md`.
- **Everything is built today.** Don't paste in code from older projects.
- Python 3.11, type hints, `ruff`-clean, small pure functions. Use `pathlib`. Log with `logging`, not prints, except for CLI output.

## Repo layout (target)
```
godshot/
  __main__.py     CLI entry (typer)          S00
  config.py       env + model IDs            S00
  schema.py       pydantic contracts         S00
  cache.py        disk cache helper          S00
  ingest.py       upload + frames            S01  Prajesh
  extract.py      Argon extraction/baseline  S02  Akash
  geometry.py     projection math            S03  Siva
  checks.py       scene → check.v1           S04  Rishika
  verify.py       Decisions API client       S05  Siva
  correct.py      Argon correction + loop    S06  Rishika
  render.py       map animation              S07  Akash
  compose.py      ffmpeg reel                S08  Akash
  web/            FastAPI + HTML UI          S09  Siva
  reshoot.py      Omni re-shoot              S12  Prajesh
eval/
  camerabench/    subset + mapped labels     S10  Rishika
  labels/         label.v1 JSONL             S10  Rishika
  score.py        metrics vs human labels    S11  Siva
  report.ipynb    charts                     S11/S13
tests/
mocks/scene_mock.json                        S00
```

## Docs index
- `.claude/docs/architecture.md`: system flow and design decisions
- `.claude/docs/contracts.md`: the JSON schemas everyone builds against
- `.claude/docs/models.md`: which Gemini model for which task, with settings
- `.claude/docs/eval.md`: evaluation design and splits
- `.claude/docs/hackathon-rules.md`: rules that can get us disqualified
- `.claude/specs/00-index.md`: spec status board and owners
