# Two-piece EJ-200 tile study

This guide describes the original Side 2 workflow with `combined/layoutStudy=false`. Its Side 2 restrictions and empty legacy `readout_events` table do not apply to the opt-in [four-layout dual-readout workflow](../combined_layouts/README.md).

This directory supports [Issue #7](https://github.com/jdbrice/g4optics/issues/7): compare a single 16 mm tile with 8+8 mm pieces, and a single 24 mm tile with 8+16 mm pieces joined using EJ-550 optical grease. The agreed study direction retains the existing beta spectrum and Side2 readout. The reversed 16+8 mm ordering is included to expose orientation dependence.

This is a bounded engineering and exploratory workflow. Its files do not establish that a run has passed: inspect the generated audit reports and the [study decision/result record](../../docs/decisions/combined-tiles-study.md). The earlier preflight work is separate evidence and must not be substituted for the compiled implementation used here.

**Readout model v2:** implementation-v1 exposed a legacy defect: a reflected photon's `StepTooSmall` relocation could report a SiPM pre-step volume, causing a false detection and premature track termination. The implementation-v1 dataset is excluded from scientific comparison. Model v2 adds explicit `boundary-entry` readout and requires fresh geometry, smoke, probe, and invalid-configuration acceptance. Historical `legacy` runs are retained only to reproduce and diagnose the previous implementation; their response values must not be mixed with model-v2 study results.

## Fixed model and coordinates

The confirmed study model uses a 100×100 mm transverse tile, bare polished outer faces, two 2.4×2.4×0.5 mm ideal SiPM volumes, and a point beta source with zero initial divergence and direction −Z, 1.5 mm above the physical entrance face. These are fixed study assumptions; laboratory calibration has not been established.

“Front” means the +Z piece facing the source. Each complete assembly is centered at z=0. Side2 means **two sensors on the same +X face**, centered at `(50.25, −25, 0)` and `(50.25, +25, 0)` mm, with copy numbers 0 and 1. It does not mean one sensor on each opposing side.

| Case | Order from source (+Z) toward −Z | Active EJ-200 thickness | Physical envelope | Source z |
|---|---|---:|---:|---:|
| `mono16` | 16 mm monolith | 16 mm | 16 mm | 9.5 mm |
| `mono24` | 24 mm monolith | 24 mm | 24 mm | 13.5 mm |
| `joined8-8` | 8 mm / 0.1 mm EJ-550 / 8 mm | 16 mm | 16.1 mm | 9.55 mm |
| `joined8-16` | 8 mm / 0.1 mm EJ-550 / 16 mm | 24 mm | 24.1 mm | 13.55 mm |
| `joined16-8` | 16 mm / 0.1 mm EJ-550 / 8 mm | 24 mm | 24.1 mm | 13.55 mm |

The sensors remain at assembly z=0 in every case. In `joined8-8`, each sensor spans the seam: its contact areas are 2.76 mm² on the front piece, 0.24 mm² on grease, and 2.76 mm² on the back piece. For either asymmetric ordering, each sensor's full 5.76 mm² face contacts the 16 mm piece. Changing ordering consequently changes both the source-facing piece and which named piece contacts the sensors; the orientation comparison should be interpreted with that geometry in mind.

The joint is a separate full-face material volume. It shares the existing EJ-550 material and material-properties table, but is independent of the existing tile–SiPM grease geometry. Side2 continues to use the inherited direct, zero-gap sensor coupling proxy. Enabling `/opnovice2/grease/enabled` is an invalid combined configuration.

## Geometry commands

These two commands are available only before initialization:

```text
/opnovice2/combined/enabled true
/opnovice2/combined/layers 8 0.1 16 mm
```

All five scientific cases, including the two monoliths, must also select:

```text
/opnovice2/sipm/readoutMode boundary-entry
```

The application default remains `legacy` for historical reproduction. `boundary-entry` counts and terminates an optical track only on a geometrical boundary step with a different post-volume named SiPM and a `Transmission`, `FresnelRefraction`, or `SameMaterial` boundary status. Reflection and `StepTooSmall` relocation are not detection conditions. Invalid mode names are fatal, and the mode command is PreInit-only. Combined geometry can still run in legacy mode for diagnosis, but that is not an accepted scientific readout model. The selected mode appears once in the construction log.

The three combined-layer values are **front plastic, joint grease, back plastic** thicknesses. `/opnovice2/tank/size 100 100 24 mm` describes the active plastic thickness, **excluding** the joint. The combined mode defaults to off. It accepts the 8/8, 8/16, and 16/8 mm plastic pairs and requires the declared tank thickness to equal their sum.

Combined mode requires Side2 (`edge-two`, +X), the sensor dimensions and fixed centers above, the EJ-200 material proxy, and bare polished outer surfaces. Steel absorbers, repeated stacks, dimples, bottom cavities, and existing SiPM grease must be disabled. Nonfinite/nonpositive dimensions, conflicting modes, missing grease optical properties, and insufficient grease spectral coverage are rejected.

The physical pieces are `TankFront` (tile index 0), `TankBack` (index 1), and `CombinedGrease`. Each tile has its own Tank→World border surface. The two tile/joint interfaces have four explicit ordered, polished dielectric boundaries, without paint or a surface material-properties table. Bulk refractive indices determine Fresnel behavior. Runtime logs report the piece/joint bounds, envelope, sensor placement, contact areas, and overlap checks.

## Optical assumptions

The fixed joint index is `n=1.46`. `prepare.py` supplies both `RINDEX` and `ABSLENGTH` through `/opnovice2/greaseProperty` before initialization. Their energy ranges must cover the configured EJ-200 `SCINTILLATIONCOMPONENT1` spectrum.

The attenuation model retains the repository's digitized EJ-550 sample-transmission data in `test/OpNovice2/optical_data/ej550_transmission_empirical.csv`, converted using

```text
L_mm = −0.1 / ln(T)
```

with an assumed 0.1 mm reference thickness. This is an **effective attenuation length**, not an independently measured pure bulk absorption coefficient. The sample transmission does not separate interface reflection and scattering; adding explicit Fresnel interfaces can therefore double-count part of the measured loss. The manifest records the CSV hash, conversion, and wavelength range. The actual 2.4982–3.1001 eV scintillation support is entirely inside the 280–500 nm transmission data, so no extrapolation of that emission spectrum is required; RINDEX is explicitly supplied over 2.0–3.3 eV. These choices are not an experimental determination of bulk attenuation.

The fixed beta source is the original `sr90_allowed_beta_v1` GPS histogram, copied from `test/OpNovice2/spectra/sr90_allowed_beta_v1_gps.mac`. It is not a fixed-energy electron and is not the radioactive-decay-chain source mode. The source distance follows the physical envelope, so a thicker assembly does not place the source inside plastic or change the intended air gap.

## Output and counter meanings

The inherited `scan` table and its original column IDs remain in place. Appended `combined_events` and `combined_boundaries` tables remain empty for monolith runs. `readout_events` is populated for every boundary-entry event, including monolith events, and remains empty in legacy mode.

- In model v2, `D` / `sipm_detected_photons` counts accepted optical boundary entries into the ideal SiPM proxy. The two active sensor counts must sum to D; inactive sensor slots must stay zero. Legacy D retains its known pre-volume miscount solely for regression. Neither mode models wavelength-dependent PDE or avalanche/electronics response.
- Legacy `G` / `generated_optical_photons` retains its original generation semantics. The fixed beta macros disable Cerenkov and wavelength shifting, and G equals the inherited scintillation count. It does **not** count all optical-parent secondary births. `D/G` is retained as a legacy diagnostic, not a complete optical efficiency or terminal-photon closure.
- Legacy tile `edep` includes optical energy deposits. The combined diagnostics record total and optical deposits separately for front, back, and joint. Their difference is reported as nonoptical deposition; optical deposition alone does not establish beta penetration into the back piece.
- `front_generated` and `back_generated` split the inherited G by production piece. `all_births_front/back/joint/other` count optical secondaries by their parent's pre-step region, including optical-parent births. These birth-region counts are distinct from inherited optical lineage.
- `lineage_front/back_to_sensor0/1` follows the inherited original optical lineage through descendants. It is not necessarily the last secondary's birth location. Unknown lineage is recorded separately and rejected by the fixed beta audit when detected photons have unknown origin.
- Boundary rows aggregate `(event, pre_region, post_region, status)` counts. Region IDs are front=0, back=1, grease=2, sensor0=3, sensor1=4, World=5, other=6, and outside-world=7. The post-region is the geometrical candidate neighbor; a reflection entry is not a successful transfer. Repeated encounters are included, so these totals are not first-crossing probabilities. `NoRINDEX` is audited separately.
- `readout_events.sensor0/1_entries` must equal the corresponding detected counts. Reflection and `StepTooSmall` columns count steps whose **post-volume candidate** is that sensor; a return relocation from a SiPM pre-volume to grease is not included in that sensor's `step_too_small` counter. Use the combined boundary table to inspect those return steps. A zero in the sensor-specific relocation column does not prove that no relocation occurred. The readout `no_rindex_steps` counter covers all observed optical geometrical boundaries.
- `hit_valid` records whether the primary crossed into a tile. The existing charged/electron entry counters include secondary tracks and are not primary-electron penetration counters. No table here gives a direct beta back-face transmission probability.

The primary exploratory response is **D per source beta event**, including zero-response and non-entering source events. Its denominator is all requested source events, not only events with `hit_valid=1`. Source-energy distribution, entry fraction, production, and energy deposits should accompany response comparisons.

## Frozen inputs and Docker execution

Prepare on the host where Git can resolve the worktree and its common repository. A worktree `.git` file can point to an absolute host path; merely mounting that worktree as `/src` in Docker does not make its Git metadata usable. Do not run `prepare.py` there unless the Git metadata is genuinely accessible. The usual split is **host preparation and source freezing; container build, execution, and audit**.

Use the selected, identified Docker image and Geant4 data installation. Mount the frozen source at `/src` and data read-only, with separate writable build/output storage at `/out`. Mount the original regression baseline read-only at `/baseline` when needed. Record the image identity, toolkit/data identity, source hashes, executable hash, generated macros, seeds, logs, and receipts. A compiled model-v2 snapshot must match the prepared manifests; a same-named preflight or model-v1 executable is not interchangeable. Do not modify the source inputs after preparation/freezing.

On the host, from the source worktree with readable Git metadata:

```sh
COMBINED_REPO="$PWD"
COMBINED_OUTPUT="$PWD/outputs/combined-tiles/NEW"
```

Replace `NEW` with a fresh run identifier. Set host `COMBINED_BASELINE` to the frozen original `20261010-baseline-v1` directory when preparing legacy regression. The new output tree is separate from that baseline and every previous implementation run. `probes.py prepare` also resolves the source path recorded in the smoke manifest, so use the host path context for that preparation.

Inside the selected configured container, whose `/out` maps to that same host `NEW` directory:

```sh
COMBINED_REPO=/src
COMBINED_OUTPUT=/out
COMBINED_EXE=/out/build/OpNovice2
COMBINED_BASELINE=/baseline
export G4RUN_MANAGER_TYPE=Serial
cmake -S "$COMBINED_REPO/test/OpNovice2" -B "$COMBINED_OUTPUT/build"
cmake --build "$COMBINED_OUTPUT/build" --parallel 4
```

Source the selected toolkit's environment and configure its read-only data paths before building/running. Python auditing requires `numpy` and `uproot`. Run the container commands below from `/src`, using the container variable values; run host commands from the source worktree, using host values. Audit flags explicitly identify source and baseline locations after relocation.

Preparation writes immutable macros and a manifest without launching Geant4. Runners launch one process per task, preserve failures, and refuse to overwrite execution evidence. The ordinary runner rejects Geant4 UI/macro failures even when the application returns exit code zero. Investigate a failed task before any separately named, explicitly accounted follow-up.

## Qualification workflow

### 1. Historical legacy regression, separate from the study

Host preparation selects legacy mode explicitly for both geometry and smoke:

```sh
python3 tools/combined_tiles/prepare.py --repo "$COMBINED_REPO" --phase geometry --readout-mode legacy --output "$COMBINED_OUTPUT/legacy-geometry"
python3 tools/combined_tiles/prepare.py --repo "$COMBINED_REPO" --phase smoke --readout-mode legacy --baseline "$COMBINED_BASELINE" --output "$COMBINED_OUTPUT/legacy-smoke"
```

Container execution and audit:

```sh
python3 tools/combined_tiles/run.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/legacy-geometry" --executable "$COMBINED_EXE"
python3 tools/combined_tiles/audit.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/legacy-geometry" --executable "$COMBINED_EXE" --report "$COMBINED_OUTPUT/legacy-geometry-audit.json"
python3 tools/combined_tiles/run.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/legacy-smoke" --executable "$COMBINED_EXE" --qualified-receipt "$COMBINED_OUTPUT/legacy-geometry-audit.json"
python3 tools/combined_tiles/audit.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/legacy-smoke" --executable "$COMBINED_EXE" --baseline "$COMBINED_BASELINE" --report "$COMBINED_OUTPUT/legacy-smoke-audit.json"
```

This fixed preparation includes five zero-event geometry tasks and five 10-event legacy smoke tasks. Only the two monoliths are compared with their original counterparts for exact scan columns, summary bytes, histogram numerics, and saved RNG states. A legacy pass proves reproduction, not correctness of its detection definition. These 50 diagnostic beta events are excluded from the corrected scientific dataset.

### 2. Corrected geometry and fifty-beta smoke

Host preparation explicitly selects the same corrected mode for **every** monolith and joined case:

```sh
python3 tools/combined_tiles/prepare.py --repo "$COMBINED_REPO" --phase geometry --readout-mode boundary-entry --output "$COMBINED_OUTPUT/study-geometry"
python3 tools/combined_tiles/prepare.py --repo "$COMBINED_REPO" --phase smoke --readout-mode boundary-entry --output "$COMBINED_OUTPUT/study-smoke"
```

Container execution and audit:

```sh
python3 tools/combined_tiles/run.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/study-geometry" --executable "$COMBINED_EXE"
python3 tools/combined_tiles/audit.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/study-geometry" --executable "$COMBINED_EXE" --report "$COMBINED_OUTPUT/study-geometry-audit.json"
python3 tools/combined_tiles/run.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/study-smoke" --executable "$COMBINED_EXE" --qualified-receipt "$COMBINED_OUTPUT/study-geometry-audit.json"
python3 tools/combined_tiles/audit.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/study-smoke" --executable "$COMBINED_EXE" --report "$COMBINED_OUTPUT/study-smoke-audit.json"
```

Geometry macros initialize and check overlaps without `/run/beamOn`. Finite overlap sampling is an engineering gate, not proof for every possible trajectory. Corrected smoke uses 10 source beta events in each case and checks readout entries against detected photons alongside the existing accounting. It does not use `--baseline`: corrected counts are not expected to reproduce a legacy readout bug. A smoke pass alone is not a scientific comparison.

### 3. Separate corrected physics probes

Host preparation uses the corrected smoke campaign:

```sh
python3 tools/combined_tiles/probes.py prepare --smoke-campaign "$COMBINED_OUTPUT/study-smoke" --output "$COMBINED_OUTPUT/physics-probes"
```

Container execution and audit:

```sh
python3 tools/combined_tiles/run.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/physics-probes" --executable "$COMBINED_EXE" --qualified-receipt "$COMBINED_OUTPUT/study-smoke-audit.json"
python3 tools/combined_tiles/probes.py audit --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/physics-probes" --executable "$COMBINED_EXE" --report "$COMBINED_OUTPUT/physics-probes-audit.json"
```

The fixed probe matrix contains 370 optical primaries and 10 monoenergetic electron primaries, in 11 processes. It exercises both joint directions, oblique total internal reflection, index-matched controls, seam-to-sensor transport, and each piece's production/accounting. D must agree with accepted sensor-entry evidence; reflected-only or relocation-only steps are not accepted as detections. Index-matched cases override grease index and attenuation for their control. Inspect stored polarization as well as source position/direction because the application can set optical polarization separately from GPS. Probe events deliberately differ from the source beta campaign and are never merged into its response statistics.

### 4. Nine invalid zero-event configurations

Host preparation:

```sh
python3 tools/combined_tiles/invalid_configs.py prepare --smoke-campaign "$COMBINED_OUTPUT/study-smoke" --output "$COMBINED_OUTPUT/invalid"
```

Container execution and audit:

```sh
python3 tools/combined_tiles/invalid_configs.py run --campaign "$COMBINED_OUTPUT/invalid" --exe "$COMBINED_EXE"
python3 tools/combined_tiles/invalid_configs.py audit --campaign "$COMBINED_OUTPUT/invalid" --output "$COMBINED_OUTPUT/invalid-audit.json"
```

The dedicated runner retains expected failures rather than using the ordinary fail-on-error runner. Cases cover zero gap, negative front thickness, inconsistent active thickness, wrong sensor layout, old grease enabled, wrapped surface, missing joint RINDEX, missing ABSLENGTH, and insufficient RINDEX coverage. Passing requires the expected exception code and specific diagnostic, plus nonzero termination or explicit fatal rejection and no evidence of physics-event execution. Command-not-found and unrelated failures do not count as successful rejection.

### 5. Qualification before exploration

The ordinary runner requires actual qualification evidence for every non-geometry phase and checks `readout_mode`, `source_files_sha256`, and `executable_sha256`. Smoke requires a matching-mode geometry audit, and physics probes require a boundary-entry smoke audit. The probe generator additionally freezes its own script while preserving the qualified core identity.

Exploration requires a combined qualification receipt with verified `status`, `smoke_status`, and **`physics_probes_status`**, `readout_mode=boundary-entry`, and matching source/executable identities. Generate it inside the container from the real gate bundles:

```sh
COMBINED_QUALIFICATION="$COMBINED_OUTPUT/qualification.json"
python3 tools/combined_tiles/qualify.py \
  --geometry "$COMBINED_OUTPUT/study-geometry-audit.json" \
  --geometry-campaign "$COMBINED_OUTPUT/study-geometry" \
  --smoke "$COMBINED_OUTPUT/study-smoke-audit.json" \
  --smoke-campaign "$COMBINED_OUTPUT/study-smoke" \
  --probes "$COMBINED_OUTPUT/physics-probes-audit.json" \
  --probes-campaign "$COMBINED_OUTPUT/physics-probes" \
  --legacy-smoke "$COMBINED_OUTPUT/legacy-smoke-audit.json" \
  --legacy-smoke-campaign "$COMBINED_OUTPUT/legacy-smoke" \
  --invalid "$COMBINED_OUTPUT/invalid-audit.json" \
  --invalid-campaign "$COMBINED_OUTPUT/invalid" \
  --baseline-campaign "$COMBINED_BASELINE" \
  --repo "$COMBINED_REPO" --executable "$COMBINED_EXE" \
  --output "$COMBINED_QUALIFICATION"
```

`qualify.py` checks report/input/output bindings, receipt identities, source and executable hashes, and reruns the read-only audits of existing ROOT data. It also verifies the full historical monolith regression and intended invalid-configuration rejections. Only after all five gate bundles pass does it write the new qualification receipt with provenance. It launches no simulations. Explicit campaign paths avoid relying on audit-parent directory defaults after host/container relocation. Do not substitute a manually written `passed` file; a rejected qualification requires investigation of its existing evidence.

## Fixed exploratory comparison

Host preparation:

```sh
python3 tools/combined_tiles/prepare.py --repo "$COMBINED_REPO" --phase explore --readout-mode boundary-entry --output "$COMBINED_OUTPUT/explore"
```

Container execution, audit, and analysis:

```sh
python3 tools/combined_tiles/run.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/explore" --executable "$COMBINED_EXE" --qualified-receipt "$COMBINED_QUALIFICATION"
python3 tools/combined_tiles/audit.py --repo "$COMBINED_REPO" --campaign "$COMBINED_OUTPUT/explore" --executable "$COMBINED_EXE" --report "$COMBINED_OUTPUT/explore-audit.json"
python3 tools/combined_tiles/analyze.py --campaign "$COMBINED_OUTPUT/explore" --audit "$COMBINED_OUTPUT/explore-audit.json" --output "$COMBINED_OUTPUT/analysis" --bootstrap-replicates 10000
```

The exploration is fixed at **five cases × two independent seed blocks × 100 source beta events = 1,000 events**, with no adaptive extension. Geometry sampling has a separate seed, and physics seeds are reset afterward. Different geometries do not claim event-by-event matched source histories. Legacy and implementation-v1 results are excluded; analysis requires corrected boundary-entry readout throughout.

The four comparisons are joined8-8−mono16, joined8-16−mono24, joined16-8−mono24, and joined16-8−joined8-16. Independent events are resampled within each seed block using at least 10,000 bootstrap replicates, yielding pointwise percentile 95% intervals for D/event differences and relative differences where defined. Intervals are not adjusted for four comparisons. There is no prespecified equivalence margin, so an interval containing zero does not establish equivalence.

Outputs include event-level CSVs, per-case summaries, combined and readout diagnostics, boundary rows, comparison intervals, and a readable report. They support a small exploratory response comparison under the fixed assumptions. They do not establish an equivalence threshold, beta penetration probability, calibrated PDE, or attribution of response changes solely to optical transport.
