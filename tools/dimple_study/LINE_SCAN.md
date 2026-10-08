# Local dimple line scan

The user accepted the native Geant4 geometry views and authorized this local
line scan on 2026-10-08. Its fixed budget is **5,200 incident electrons**.
No OSC execution is needed for this batch.

## Fixed design

- Frozen simulation source: `5d6c50d37e33178e27710df5cc13e0127593acbe`.
  Reuse the exact executable accepted in `optical-v1`; no C++ or physics changes.
- Flat versus radius/depth 3 mm dimple, single 100 × 100 × 5 mm tile,
  external 6 × 6 × 0.5 mm SiPM at (0, 0, −2.75) mm, bare polished,
  finite-absorption air and no grease. Material settings are those documented
  in [the optical check](OPTICAL_CHECK.md).
- One 1 MeV electron per event, source (x, 0, 4) mm, direction −Z.
- x positions in mm:
  `−40, −10, −5, −3.2, −2.8, −1.5, 0, 1.5, 2.8, 3.2, 5, 10, 40`.
- Two independently seeded 100-event blocks for each geometry/position:
  **52 tasks, 200 events per configuration**. All 104 seed values are distinct
  and excluded from the prior dimple engineering runs. Simulation seeds are
  reset after geometry sampling. Geometries are not statistically paired.
- Local Geant4 11.4.2, linux/arm64, Serial; one process at a time, one CPU and
  4 GiB container memory. Each task has a 600-second timeout. No retries,
  replacements, removal of zero responses, or automatic sample extensions.

## Measurements and boundaries

The primary measurement is mean collected photons D per incident electron,
including zero-response events. Report mean legacy G and `sum(D)/sum(G)`
separately. The latter is the ratio of totals, not mean event efficiency and
not a complete photon-birth efficiency. Keep the two block means alongside
the combined 200-event estimate.

Compare dimple minus flat at each x using independent samples. No significance
claim or confidence interval is produced by the runner. If intervals are added
later, resample whole events, preserve D/G covariance and keep geometry samples
independent. This nonuniform one-dimensional grid is not a tile-area average
or a two-dimensional uniformity measurement.

Every task retains ROOT, CSV, log, final RNG state, runtime receipt and audit.
The existing optical audit runs with `require_response=false`: G>0/D=0 is valid;
G=0/D=0 has undefined efficiency and is retained with the validity flag false.
Count, geometry, source and birth/start invariants remain enforced. A failed
process, changed identity or failed audit stops the campaign and preserves it.

## Freeze and execute

Commit the tools first, then prepare a new directory:

```sh
python3 -B tools/dimple_study/line_scan.py prepare \
  --batch outputs/dimple-study/line-v1 \
  --accepted outputs/dimple-study/optical-v1
```

Record the printed manifest SHA256. The campaign holds a copy of the accepted
binary, the simulation source, optical template and frozen runtime tools.
Run the frozen script in the existing immutable Docker image, mounting this
campaign at `/campaign`, its `runtime` directory read-only at
`/campaign/runtime`, and the accepted data volume read-only at `/g4data`:

```sh
python3 -B /campaign/runtime/tools/dimple_study/line_scan.py run \
  --batch /campaign --manifest-sha256 RECORDED_MANIFEST_SHA256
```

Use image
`sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee`,
`--network none --cpus 1 --memory 4g`, and no automatic container restart.
Full dataset checksum verification precedes the first event. The runtime,
binary, source record and all macros are verified before/after each task.

`progress.json` reports prepared/running/completed/stopped and accepted counts.
`STOP.json` records a failure without retrying. `verification.json` and
`line-summary.csv` are written only after all 52 tasks are accepted. Completion
of the process alone is not acceptance. The summary has no uncertainty or
full-tile-efficiency claim.
