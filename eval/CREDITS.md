# Credits

Every non-own clip used in evaluation or demos must be listed here with its source, license, and modifications. **No copyrighted film footage is allowed.**

| Clip | Source | License | Modifications |
|---|---|---|---|
| `cb_001`–`cb_040` | CameraBench (`syCen/CameraBench`, Hugging Face) | CC BY 4.0 | Sampled 40 clips (seed 0) with mapped camera-motion tags; GIF→MP4 conversion for pipeline input. **Numbers only — not shown in the demo.** |
| `tos_001`–`tos_010` | *Tears of Steel* (Blender Foundation, `https://download.blender.org/demo/movies/ToS/tears_of_steel_720p.mov`) | CC BY 3.0 | Trimmed 10 zero-cut continuous shots (7.0–9.5 s) via `ffmpeg` (`tos_001`–`004` dev, `tos_005`–`010` test) |
| `sintel_001` | *Sintel* (Blender Foundation, `https://download.blender.org/durian/trailer/sintel_trailer-720p.mp4`) | CC BY 3.0 | Trimmed 1 zero-cut continuous shot (5.9 s, dev domain-shift probe) via `ffmpeg` |
| `pexels_*` *(pending)* | Pexels (insert per-clip creator URLs) | Pexels License (free) | Trimmed into 5–12 s single continuous shots |
| `own_*` *(pending)* | Venue clips filmed by consenting non-team participants | Signed participant consent (`eval/consent/`, gitignored) | Trimmed into 5–15 s shots (+ one 30 s long-take stress test) |
