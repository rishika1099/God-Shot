# Models: which Gemini model does what

All IDs are read from `.env` through `godshot/config.py`. **VERIFY each ID with `client.models.list()` at setup**, since these models are new and IDs/fields may differ from what's written here.

| Task | Spec | Model (env var) | Settings | Why |
|---|---|---|---|---|
| Scene extraction (also BASELINE) | S02 | `gemini-4-argon` (`GODSHOT_MODEL_EXTRACT`) | static video processing, `fps=4` (6–8 for whip pans), `thinking_level=MEDIUM`, JSON schema enforced | Best video + spatial reasoning; structured output |
| Scene correction | S06 | `gemini-4-argon` (`GODSHOT_MODEL_CORRECT`) | same video URI + failing frames as high-res image parts, `thinking_level=HIGH` | Needs careful reasoning over failures |
| Verification | S05, S11, S12 | `gemini-decision-1` (`GODSHOT_MODEL_VERIFY`) via Decisions API (`api_version="v1beta"`) | `noul` / `choice` questions, one request per frame or frame pair, up to 1000 questions/request | Fast, cheap, calibrated probabilities; no text generation |
| Cheap helper / ablation | S12, S11 | `gemini-4-hydrogen` (`GODSHOT_MODEL_CHEAP`) | `thinking_level=LOW` | Shot-description prompt writing; "model size" ablation |
| Re-shoot | S12 | `gemini-omni-1.2-flash` (`GODSHOT_MODEL_RESHOOT`) via Interactions API | `response_format={"type":"video","duration":"5s"–"8s","resolution":"360p"→"720p","aspect_ratio":"16:9"}`, ≤6 reference images | Video generation from references + text |
| Stylized room background (stretch) | S07 | image model (`GODSHOT_MODEL_IMAGE`, set when verified) | one image per clip | Pretty background layer under the map |

## Verified so far (2026-11-21, S00)
- SDK: `google-genai==3.6.1` (requirements pin `>=3.6`). `Client` exposes `decisions`, `interactions`, `models`, `files`.
- Decisions wire format: noul = `{"type":"noul","instructions":str,"criteria":{"true_description","false_description"}}`; choice = `{"type":"choice","instructions":str,"criteria":{key: description}}` (2–255 options). Our `check.v1` keeps `options` and `Question.to_api()` maps it to `criteria`.
- Decisions answers: noul → `noul` (our `p_yes`), choice → `probabilities` (our `choice_probs`) + `choice`; both carry `confidence`. Also a `score` question type exists (unused).
- `types.ThinkingConfig(thinking_level=...)` accepts `MINIMAL | LOW | MEDIUM | HIGH | XHIGH`.
- **Model IDs VERIFIED** with `python -m godshot smoke` (team key, 2026-11-21). All `.env.example` defaults exist:
  - `models/gemini-4-argon` (EXTRACT, CORRECT): live call OK, 7.2 s for a one-word reply at `thinking_level=LOW`
  - `models/gemini-4-hydrogen` (CHEAP)
  - `models/gemini-decision-1` (VERIFY): live `noul` call OK, 0.7 s, p_yes=0.95 on an obvious yes
  - `models/gemini-omni-1.2-flash` (RESHOOT)
  - Also available, unused so far: `gemini-4-carbon`, `gemini-4-neon`, `gemini-omni-1.1-flash`, `gemini-omni-flash-preview`.
  - `GODSHOT_MODEL_IMAGE` stays empty: no image model chosen yet (S07 stretch).
- `generate_content` logs "AFC is enabled" (automatic function calling); harmless since we pass no tools.

## Notes and gotchas (from our research; verify)
- Gemini 4: sampling params (temperature/top_p) are reportedly ignored. Control output with `thinking_level` + schema.
- Upload the clip ONCE via the Files API and reuse the URI for baseline, loop and corrections.
- Timestamps in prompts: `MM:SS.sss` when sampling above 1 fps.
- Decisions API: question instructions are TEXT ONLY and the model never sees question keys, so every instruction must be a full sentence. `choice` keys are sorted alphabetically; use `other` as the catch-all. `noul` returns p(yes) clamped to 0.01–0.99.
- Decisions has no context caching: identical inputs are billed twice (it's cheap anyway). Use our disk cache.
- Optional re-shoot backend (S12): `fal-ai/wan-vace-14b` (`GODSHOT_MODEL_WAN`, task `GODSHOT_WAN_TASK=depth`), chosen with `GODSHOT_RESHOOT_BACKEND=wan_vace`, needs `FAL_KEY`. Guided modes only; text mode stays on Omni.
- Omni: ≤15 s per output; generations take ~30–90 s; run in parallel in the background; never generate live on stage.
- Today we have Tier 3 credits for all Gemini 4 models. Still set a billing cap.

## Snippets (VERIFY field names)
```python
from google import genai
from google.genai import types
client = genai.Client(http_options={"api_version": "v1beta"})

# Extraction
f = client.files.upload(file="clip.mp4")          # poll until f.state.name == "ACTIVE"
resp = client.models.generate_content(
    model=cfg.MODEL_EXTRACT,
    contents=[types.Part(file_data=types.FileData(file_uri=f.uri, mime_type="video/mp4"),
                         video_metadata=types.VideoMetadata(fps=cfg.FPS)),
              EXTRACTION_PROMPT],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=SceneV1.model_json_schema(),
        thinking_config=types.ThinkingConfig(thinking_level="MEDIUM")))

# Verification (field names VERIFIED against google-genai 3.6.1 source, 2026-11-21)
res = client.decisions.create(
    model=f"models/{cfg.MODEL_VERIFY}",                      # SDK docstring: format `models/{model}`
    input=[{"type": "text", "text": "A single frame from a film shot at 00:02.000."},
           {"type": "image", "mime_type": "image/jpeg", "data": b64}],   # verified block shape
    questions={str(i): c.question.to_api() for i, c in enumerate(frame_checks)})  # keys are opaque to the model
ans = res.answers["0"]          # NoulAnswer: .noul = P(true) in [0.01, 0.99], .confidence = max(noul, 1-noul)
                                # ChoiceAnswer: .choice (top key), .probabilities {key: p}, .confidence

# Re-shoot
it = client.interactions.create(model=cfg.MODEL_RESHOOT, input=[...],
     response_format={"type": "video", "duration": "8s", "resolution": "360p"})
```
