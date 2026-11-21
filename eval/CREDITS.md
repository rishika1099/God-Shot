# Credits

Every non-own clip used in evaluation or demos must be listed here with its source, license, and modifications. **No copyrighted film footage is allowed.**

| Clip | Source | License | Modifications |
|---|---|---|---|
| `cb_001`–`cb_040` | CameraBench (`syCen/CameraBench`, Hugging Face) | CC BY 4.0 | Sampled 40 clips (seed 0) with mapped camera-motion tags; GIF→MP4 conversion for pipeline input. **Numbers only — not shown in the demo.** |
| `tos_*` *(pending)* | *Tears of Steel* (Blender Foundation) | CC BY 3.0 | Trimmed into 5–12 s single continuous shots via `ffmpeg` |
| `sintel_*` / `spring_*` / `charge_*` *(pending)* | Blender Open Movies (Blender Foundation) | CC BY 3.0 / CC BY 4.0 | Trimmed into 5–12 s single continuous shots (domain-shift probe) |
| `pexels_*` *(pending)* | Pexels (insert per-clip creator URLs) | Pexels License (free) | Trimmed into 5–12 s single continuous shots |
| `own_*` *(pending)* | Venue clips filmed by consenting non-team participants | Signed participant consent (`eval/consent/`, gitignored) | Trimmed into 5–15 s shots (+ one 30 s long-take stress test) |
