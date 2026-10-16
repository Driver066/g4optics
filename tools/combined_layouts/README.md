# One-assembly, four-layout, dual-readout beta study

This workflow extends the [combined-tile study](../combined_tiles/README.md) across four SiPM layouts while retaining both requested readout definitions. Every configuration contains **one monolith or one joined assembly**, illuminated by the original `sr90_allowed_beta_v1` spectrum. It does not build a ten-layer steel stack or substitute a neutron source.

The fixed scientific matrix is **2 readouts × 4 layouts × 5 geometries = 40 configurations**, each with two independent 100-event seed blocks: **8,000 source beta events in 80 processes**. Engineering qualification is separate. See the [decision and result record](../../docs/decisions/combined-layouts-dual-readout.md) for the observed execution and conclusions; this guide does not claim a scientific result merely because inputs were prepared.

## Geometry and the three different meanings of “gap”

All plastic has a 100×100 mm transverse footprint. The assembly center is z=0, the source is on +Z, and the initial beam direction is −Z. “Front” is the source-facing plastic piece. The point source has zero initial angular divergence and starts 1.5 mm above the physical entrance surface. Outer surfaces are bare polished. Each ideal SiPM has a 2.4×2.4 mm² active face and 0.5 mm normal thickness; PDE and electronics are not modeled.

| Geometry ID | Order from +Z toward −Z | Plastic thickness | Envelope thickness | Source z |
|---|---|---:|---:|---:|
| `mono16` | one 16 mm tile | 16 mm | 16 mm | 9.5 mm |
| `mono24` | one 24 mm tile | 24 mm | 24 mm | 13.5 mm |
| `joined8-8` | 8 mm / 0.1 mm EJ-550 / 8 mm | 16 mm | 16.1 mm | 9.55 mm |
| `joined8-16` | 8 mm / 0.1 mm EJ-550 / 16 mm | 24 mm | 24.1 mm | 13.55 mm |
| `joined16-8` | 16 mm / 0.1 mm EJ-550 / 8 mm | 24 mm | 24.1 mm | 13.55 mm |

Let `H = envelope_thickness / 2`. The following are world-coordinate sensor centers in mm, for both readout modes:

| Study layout ID | Detector command value | Centers | Count | Total active area |
|---|---|---|---:|---:|
| `side-two` | `edge-two`, face +X | `(50.25, −25, 0)`, `(50.25, +25, 0)` | 2 | 11.52 mm² |
| `back-center` | `back-center`, face −Z | `(0, 0, −H−0.25)` | 1 | 5.76 mm² |
| `back-two` | `back-two`, face −Z | `(−25, −25, −H−0.25)`, `(+25, +25, −H−0.25)` | 2 | 11.52 mm² |
| `back-four` | `back-four`, face −Z | all four `(±25, ±25, −H−0.25)` combinations | 4 | 23.04 mm² |

There is one sensor group on the whole assembly. Side2 uses the same +X face, not opposing sides. Back2 uses a diagonal pair. Back sensors contact the final external −Z plastic face; none are placed at the internal plastic–grease interface.

These lengths have different roles:

- **0.1 mm EJ-550** is the physical joint between two plastic pieces and remains present for all joined layouts.
- **0.5 mm SiPM thickness** occupies space outside the assembly. Its active face directly touches plastic; it does not imply a tile–SiPM air gap.
- The **nine 0.5 mm inter-module spaces** in the separate steel layout study accommodated sensors between one tile and the next steel plate. They are not part of this standalone assembly. No extra internal air layer is introduced for Back layouts.

The back-sensor position uses the complete envelope, including the 0.1 mm joint. Using the legacy plastic-only half-thickness would place the sensor 0.05 mm too far inward and overlap the rear plastic. The standalone layout mode uses `−H−0.25 mm` explicitly and checks actual box bounds and contact areas.

For `joined8-8`, each Side2 active face contacts front/grease/back over 2.76/0.24/2.76 mm². For either asymmetric joined ordering, each Side2 sensor contacts the full 5.76 mm² on the 16 mm piece. Every Back sensor instead contacts 5.76 mm² on the **back piece**, whether that piece is 8 or 16 mm thick. Changing layout therefore changes physical coupling as well as sensor count and area.

## Opt-in commands and material model

All new-study macros select these commands before initialization:

```text
/opnovice2/combined/layoutStudy true
/opnovice2/sipm/readoutMode legacy
```

or `boundary-entry` for the other readout half of the matrix. The layout-study flag applies to monoliths and joined assemblies; `/opnovice2/combined/enabled` independently selects whether the plastic is split. For example:

```text
/opnovice2/combined/enabled true
/opnovice2/combined/layers 8 0.1 16 mm
/opnovice2/tank/size 100 100 24 mm
/opnovice2/sipm/layout back-two
/opnovice2/sipm/face -Z
/opnovice2/sipm/localPosition 0 0 0 mm
/opnovice2/sipm/size 2.4 2.4 0.5 mm
```

The layer vector is front plastic, grease, back plastic. `tank/size` excludes grease. The new flag defaults to false so existing geometry paths remain available. The fixed study rejects steel, repeated stacks, dimples, cavities, and the older tile–SiPM grease option. It checks allowed dimensions and faces, zero coupling distance, full sensor contact, and absence of positive-volume overlap. Runtime `Standalone layout study`, `Standalone volume bounds`, `Standalone sensor contact`, and geometry-PASS lines provide actual placement evidence.

The joint uses EJ-550 with `n=1.46` and the existing transmission-derived effective attenuation model, `L_mm = −0.1 / ln(T)`. The digitized sample data are not an independent measurement of pure bulk absorption: reflection and scattering losses are not separated. EJ-200's configured emission support lies within the data wavelength range. The joint optical properties, input hashes, source spectrum, and fixed assumptions are stored in each campaign manifest. The chosen setup is not a laboratory calibration.

## Two readout definitions and passive diagnostics

Both modes are preserved, identified in every configuration, and reported separately:

- **`legacy`:** the historical tally counts and stops an optical track when its pre-step volume is a SiPM. A reflected boundary recovery step can satisfy that condition without physical sensor entry. Its D is the historical tally, including the known false-detection behavior.
- **`boundary-entry`:** D counts an optical geometrical-boundary step into a different post-volume named SiPM only when the boundary status is `Transmission`, `FresnelRefraction`, or `SameMaterial`. Reflection and `StepTooSmall` relocation do not trigger detection or track termination.

With `combined/layoutStudy true`, `readout_events` records passive actual-entry, reflection, and candidate-StepTooSmall counters in **both** modes. Boundary-entry D must equal accepted sensor entries. Legacy D can exceed observed actual entries.

**Subtracting observed entries from legacy D does not reconstruct corrected transport.** The legacy false kill changes what happens afterward: the reflected photon and any later interactions are absent from that history. Passive entries are measured along the legacy history, while boundary-entry mode has its own independently simulated history. The exported legacy excess is a within-run diagnostic, not the number of detections that a corrected counterfactual would have produced. The modes are neither pooled nor compared as paired events.

Original `scan` columns and inherited counters remain present:

- G retains the legacy generation definition and excludes optical-parent secondary births; D/G is not a complete optical efficiency.
- Tile energy deposition includes optical deposition. Combined diagnostics split front/back/joint total and optical deposits so nonoptical deposition can be derived.
- Birth region and inherited original optical lineage are different. Per-sensor lineage totals include explicit slots for sensors 0–3; inactive slots must be zero.
- Boundary rows count repeated candidate-neighbor encounters, including reflections and relocation steps. They do not provide unique-track paths, first-crossing probabilities, or complete terminal-photon closure. Sensor-specific `step_too_small` records a sensor **post-candidate**, not every return step whose pre-volume was that sensor.
- `hit_valid` identifies primary entry, while response means include all source events, including misses and zero responses. The outputs are not beta back-face penetration probabilities.

Region IDs preserve the earlier meanings and append two new sensor IDs:

| Region | ID |
|---|---:|
| front plastic / monolith | 0 |
| back plastic | 1 |
| joint grease | 2 |
| sensor 0 | 3 |
| sensor 1 | 4 |
| World | 5 |
| other volume | 6 |
| outside World | 7 |
| sensor 2 | 8 |
| sensor 3 | 9 |

## Execution environments and evidence

The Git checkpoint includes code, documentation, and small summary/figure assets only. Full run evidence and the original `20261010-implementation-v2` regression reference remain local under ignored `outputs/`. A fresh clone must obtain that reference evidence separately before running the historical regression and complete qualification; the published summaries are not substitutes for the required ROOT, logs, macros, and RNG records.

Preparation runs on the **host with readable Git metadata**. A worktree `.git` file can reference an absolute host path, so mounting it as `/src` in Docker does not necessarily make Git usable there. `prepare.py` also reserves seeds from prior `outputs/combined-tiles/**/manifest.json`, including rejected attempts. Keep those manifests accessible during host preparation.

Freeze the identified source/tool inputs, then mount that snapshot read-only at `/src`, the selected Geant4 data read-only, the new output directory writable at `/out`, and the v2 regression reference read-only at `/reference-v2`. Record image, toolkit/data, source, executable, macro, seed, and output identities. Use the same qualified binary throughout; filenames alone do not establish model identity. Python auditing/analysis needs `numpy` and `uproot`.

Host variables, from the source worktree:

```sh
LAYOUT_REPO="$PWD"
LAYOUT_OUTPUT="$PWD/outputs/combined-layouts/NEW"
LAYOUT_REFERENCE="$PWD/outputs/combined-tiles/20261010-implementation-v2"
```

Replace `NEW` with a fresh identifier and set the reference variable to its actual location if different. In the configured container, from `/src`, use container-visible paths:

```sh
LAYOUT_REPO=/src
LAYOUT_OUTPUT=/out
LAYOUT_REFERENCE=/reference-v2
LAYOUT_EXE=/out/build/OpNovice2
export G4RUN_MANAGER_TYPE=Serial
cmake -S "$LAYOUT_REPO/test/OpNovice2" -B "$LAYOUT_OUTPUT/build"
cmake --build "$LAYOUT_OUTPUT/build" --parallel 2
```

The following sections label host preparation separately from container execution. `prepare.py` generates both modes and all four layouts automatically; it has no per-mode selector. Each task is its own Serial Geant4 process. `run.py --workers 2` permits at most two independent processes concurrently. On the first observed failure it stops dispatching new tasks, preserves receipts from already running tasks, and records tasks not started. It does not automatically retry or overwrite evidence. A timeout, nonzero exit, UI error despite exit zero, missing output, or audit failure requires inspection before a separately named follow-up.

## Engineering gates

| Gate | Processes | Physics events | Role |
|---|---:|---:|---|
| Geometry | 40 | 0 | Actual bounds, gaps, counts, contacts, overlaps |
| Smoke | 40 | 200 beta | Five events per configuration and event-level accounting |
| v2 Side2 regression | 20 | 200 beta | Exact old behavior with new flag omitted and enabled |
| Optical probes | 22 | 1,100 optical primaries | Every active sensor and Side2 seam, in both modes |

These are engineering evidence, not additional observations in the 8,000-beta analysis.

### Geometry and smoke

Host:

```sh
python3 tools/combined_layouts/prepare.py --repo "$LAYOUT_REPO" --phase geometry --output "$LAYOUT_OUTPUT/geometry"
python3 tools/combined_layouts/prepare.py --repo "$LAYOUT_REPO" --phase smoke --output "$LAYOUT_OUTPUT/smoke"
```

Container:

```sh
python3 tools/combined_layouts/run.py --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/geometry" --executable "$LAYOUT_EXE" --workers 2
python3 tools/combined_layouts/audit.py --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/geometry" --executable "$LAYOUT_EXE" --report "$LAYOUT_OUTPUT/geometry-audit.json"
python3 tools/combined_layouts/run.py --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/smoke" --executable "$LAYOUT_EXE" --qualified-receipt "$LAYOUT_OUTPUT/geometry-audit.json" --workers 2
python3 tools/combined_layouts/audit.py --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/smoke" --executable "$LAYOUT_EXE" --report "$LAYOUT_OUTPUT/smoke-audit.json"
```

Geometry macros do not call `beamOn`. Geometry sampling uses a separate seed pair, and the exact physics seeds are reset after geometry checks. Positive-event gates require complete event coverage, active/inactive sensor accounting, readout diagnostics, and saved RNG evidence.

### Exact Side2 regression against v2

Host:

```sh
python3 tools/combined_layouts/regression.py prepare --reference "$LAYOUT_REFERENCE" --output "$LAYOUT_OUTPUT/regression"
```

Container:

```sh
python3 tools/combined_layouts/regression.py run --campaign "$LAYOUT_OUTPUT/regression" --exe "$LAYOUT_EXE"
python3 tools/combined_layouts/regression.py audit --campaign "$LAYOUT_OUTPUT/regression" --reference "$LAYOUT_REFERENCE" --exe "$LAYOUT_EXE" --output "$LAYOUT_OUTPUT/regression-audit.json"
```

This uses the ten original v2 `legacy-smoke`/`smoke` tasks and their exact macros/seeds twice. In the `omitted` variant the original macro bytes are unchanged; in `enabled`, only `/opnovice2/combined/layoutStudy true` is inserted before initialization. The reference path can be relocated with `--reference`; hashes bind the actual original artifacts, without incorrectly requiring changed live C++ to equal the old v2 source.

The audit demands exact original table-column values, row coverage, summary bytes, original histogram bins/axes/variances/statistics, and final RNG bytes. Added sensor-2/3 columns must be zero for Side2. Only enabled legacy mode may add passive `readout_events` rows where v2 had none; that exception does not relax the old scan or physics equality checks. Regression uses its own sequential runner and refuses retry into an existing execution directory.

### Optical probes

Host, after the smoke campaign has been prepared:

```sh
python3 tools/combined_layouts/probes.py prepare --smoke-campaign "$LAYOUT_OUTPUT/smoke" --output "$LAYOUT_OUTPUT/probes"
```

Container, after the smoke audit passes:

```sh
python3 tools/combined_layouts/run.py --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/probes" --executable "$LAYOUT_EXE" --qualified-receipt "$LAYOUT_OUTPUT/smoke-audit.json" --workers 2
python3 tools/combined_layouts/probes.py audit --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/probes" --executable "$LAYOUT_EXE" --report "$LAYOUT_OUTPUT/probes-audit.json"
```

The probes use the joined8-8 geometry, one directed 3 eV optical photon per event, and 50 events per probe. They illuminate each active sensor from its adjacent plastic and additionally exercise both Side2 seam contacts, in both readout modes. Their fixed direction/polarization is a diagnostic stimulus, not the source-beta distribution. Probe outputs check the intended sensor, true boundary-entry evidence, and the distinct legacy tally behavior. They are excluded from beta-response estimates.

### Qualification from real gate evidence

Container:

```sh
python3 tools/combined_layouts/qualify.py \
  --geometry "$LAYOUT_OUTPUT/geometry-audit.json" --geometry-campaign "$LAYOUT_OUTPUT/geometry" \
  --smoke "$LAYOUT_OUTPUT/smoke-audit.json" --smoke-campaign "$LAYOUT_OUTPUT/smoke" \
  --probes "$LAYOUT_OUTPUT/probes-audit.json" --probes-campaign "$LAYOUT_OUTPUT/probes" \
  --regression "$LAYOUT_OUTPUT/regression-audit.json" --regression-campaign "$LAYOUT_OUTPUT/regression" \
  --regression-reference "$LAYOUT_REFERENCE" \
  --repo "$LAYOUT_REPO" --executable "$LAYOUT_EXE" --output "$LAYOUT_OUTPUT/qualification.json"
```

`qualify.py` re-audits raw gate outputs read-only, compares them with saved reports, verifies source and binary identity, and checks that downstream gates used the actual upstream qualifications. It also reruns the independent v2 regression audit. Only verified evidence produces the new qualification receipt. Do not replace this with a manually written `passed` file.

## Fixed 8,000-beta run and analysis

Host preparation:

```sh
python3 tools/combined_layouts/prepare.py --repo "$LAYOUT_REPO" --phase explore --output "$LAYOUT_OUTPUT/explore"
```

Container execution and analysis:

```sh
python3 tools/combined_layouts/run.py --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/explore" --executable "$LAYOUT_EXE" --qualified-receipt "$LAYOUT_OUTPUT/qualification.json" --workers 2
python3 tools/combined_layouts/audit.py --repo "$LAYOUT_REPO" --campaign "$LAYOUT_OUTPUT/explore" --executable "$LAYOUT_EXE" --report "$LAYOUT_OUTPUT/explore-audit.json"
python3 tools/combined_layouts/analyze.py --campaign "$LAYOUT_OUTPUT/explore" --audit "$LAYOUT_OUTPUT/explore-audit.json" --output "$LAYOUT_OUTPUT/analysis"
```

There are exactly 200 source events per configuration, in two independent seed blocks. Modes, layouts, geometries, blocks, and phases use distinct registered seeds. All source events, including non-entering and zero-response events, remain in the denominator. The budget has no adaptive extension.

The main comparisons are **joining ratios within the same layout and readout mode**:

- joined8-8 / mono16;
- joined8-16 / mono24;
- joined16-8 / mono24.

The analysis separately exports orientation contrasts, readout-definition contrasts, and layout×joining ratio-of-ratios `Q` relative to Side2. Absolute D across layouts also changes sensor count and total area. D per mm² is descriptive; dividing by area does not isolate a causal effect of placement.

Uncertainty uses 10,000 whole-event bootstrap replicates with independent resampling within each seed block. A single joint matrix of configuration means is reused for all contrasts, so the shared mono24 control receives the **same resampled mean within each replicate** wherever that control appears. This preserves the shared-control dependence without pretending that events from different configurations are paired.

Intervals are pointwise descriptive percentile 95% intervals, without multiple-comparison adjustment. There is no equivalence margin or equivalence test; a ratio interval containing 1, or a difference interval containing 0, does not establish equivalence. Undefined ratio replicates are explicitly reported.

Outputs include per-event data, four-sensor readout records, combined/boundary diagnostics, summaries, joining/orientation/readout contrasts, interaction Q values, the reusable bootstrap mean matrix, and a readable report. They describe the two tally definitions under this fixed simplified model. They do not establish laboratory PDE, exact beta penetration probabilities, unique optical trajectories, or a causal decomposition of response changes into grease absorption, interface optics, and placement effects.
