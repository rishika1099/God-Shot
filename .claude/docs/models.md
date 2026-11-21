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

## Notes and gotchas (from our research; verify)
- Gemini 4: sampling params (temperature/top_p) are reportedly ignored. Control output with `thinking_level` + schema.
- Upload the clip ONCE via the Files API and reuse the URI for baseline, loop and corrections.
- Timestamps in prompts: `MM:SS.sss` when sampling above 1 fps.
- Decisions API: question instructions are TEXT ONLY and the model never sees question keys, so every instruction must be a full sentence. `choice` keys are sorted alphabetically; use `other` as the catch-all. `noul` returns p(yes) clamped to 0.01–0.99.
- Decisions has no context caching: identical inputs are billed twice (it's cheap anyway). Use our disk cache.
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

# Verification
res = client.decisions.create(
    model=cfg.MODEL_VERIFY,
    input=[{"type": "text", "text": "A single frame from a film shot at 00:02.000."},
           {"type": "image", "mime_type": "image/jpeg", "data": b64}],   # VERIFY block shape
    questions={c.check_id: c.question.model_dump() for c in frame_checks})

# Re-shoot
it = client.interactions.create(model=cfg.MODEL_RESHOOT, input=[...],
     response_format={"type": "video", "duration": "8s", "resolution": "360p"})
```
