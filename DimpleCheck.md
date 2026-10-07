# Dimple check scanning

We need to perform three groups of scanning to see the collection efficiency:
1. Adding a half sphere on the top of SiPM
2. Replacing the dimple with a cube with same volume
3. Compare no dimple with dimple, but this time enlarge the SiPM so it can cover the gap of the dimple

Prioritize number 3 first (because it might directly answer their question about why adding a dimple will make the collection efficiency goes down), but still do groups 1 & 2.

## Agreed preparation, 2026-10-07

Begin group 3 from commit `404a11bc` on `codex/dimple-study`. Use a
100 × 100 × 5 mm tile, a radius/depth 3 mm hemisphere, and a 6 × 6 × 0.5 mm
SiPM outside the original bottom face. The flat control uses the same sensor
size and position. Start with bare polished surfaces, an air cavity, and no
optical grease. The new placement is named `external`; the existing `opening`
and `surface` placements remain separate historical controls.

See [preparation and validation](tools/dimple_study/README.md). Geometry checks
do not constitute an optical-efficiency scan or acceptance of the old W08
diagnostic baseline.
