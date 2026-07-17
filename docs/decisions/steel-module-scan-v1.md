# Steel Module Scan v1 — Decision Record and Implementation Contract

Status: analysis-v2 reviewed; fixed-reference absorber and 10% staged
production policies accepted; execution infrastructure pending
Study preset: `steel-module-scan-v1`
Last updated: 2026-07-17

## Purpose and relationship to the earlier neutron study

This record is derived from
`docs/decisions/realistic-detector-neutron-variant.md`, but it defines a new
experiment rather than revising the historical `realistic-neutron-v1` experiment in place.

The original preset, its `432.58 MeV` neutron input, completed benchmark, and
generated-but-unsubmitted convergence pilot remain immutable historical
artifacts. They may inform engineering, regression, and runtime expectations,
but they are not statistical evidence for this scan. All accepted data for the
new scan must identify `study_preset=steel-module-scan-v1`.

The immediate scientific question remains the same: determine whether the
greater neutron-induced light production of thicker tiles outweighs their
poorer optical collection. The new requirement expands the comparison to six
tile thicknesses and three SiPM layouts on a `100 x 100 mm` tile.

## Confirmed decisions

### SMS-001 — Primary scientific outputs

Keep the causal stages distinguishable. For every incident neutron, preserve
enough information to report at least:

- primary-neutron steel interaction and steel energy deposition;
- charged-particle entry and energy deposition in the scintillator;
- scintillation and total generated optical photons;
- optical collection efficiency;
- total photons entering any SiPM proxy volume;
- for multi-SiPM layouts, photons entering each sensor copy separately.

“SiPM detected photons” remains a geometry-level optical-entry proxy. It does
not include a measured photon-detection efficiency or electronics response.

### SMS-002 — Neutron source authority

The professor's latest instruction supersedes the momentum wording in
`SteelModuleScan.md` for this preset:

```text
neutron kinetic energy = 1 GeV
```

Kinetic energy is the authoritative source quantity. Using
`m_n c^2 = 0.9395654205 GeV`, the derived quantities are:

```text
E_total = 1.939565421 GeV
p       = 1.696800177 GeV/c
```

The generated Geant4 source commands must contain:

```text
/gps/particle neutron
/gps/energy 1000 MeV
```

The derived momentum and total energy are metadata only. No code path may
convert the old `1 GeV/c` requirement into `432.58 MeV` for this preset.

### SMS-003 — Centered pencil-gun interpretation

Interpret the professor's instruction, “shoot it as a gun, the location
shouldn't matter now - just shoot at the center for now,” as a monoenergetic,
point-like, zero-divergence beam at the tile center, traveling from `+Z` toward
`-Z` along the tile normal.

The implementation may continue to use `G4GeneralParticleSource`; “gun” fixes
the physical distribution, not the Geant4 source class. Therefore:

- transverse source center: `x = 0`, `y = 0`;
- transverse profile: point;
- angular model: pencil;
- direction: `(0, 0, -1)`;
- no `55 mrad` or other inherited electron-beam divergence.

Off-center, one-dimensional, and two-dimensional source scans are outside v1
unless a later requirement explicitly adds them.

### SMS-004 — Steel absorber and source clearance

Retain the accepted backward-ePIC local absorber proxy:

- material: `StainlessSteelSAE304`;
- density: `7.9 g/cm3`;
- mass fractions: 74% Fe, 18% Cr, 8% Ni;
- beam-axis thickness: `40 mm`;
- fixed v1 production reference: `500 x 500 x 40 mm`;
- pilot-only transverse diagnostics: `200` and `300 mm`.

The official ePIC absorber is continuous rather than one plate per readout
tile. The rectangular slab remains a local proxy, and every v1 interpretation
is conditional on its fixed `500 mm` transverse extent. SMS-015 records the
accepted policy and the conditions that would reopen a larger-size study or an
official-geometry study; v1 does not claim transverse convergence.

Keep the tile centered at the origin. With tile thickness `t`, derive:

```text
z_tile_front     = t / 2
z_steel_center   = t / 2 + 20 mm
z_steel_upstream = t / 2 + 40 mm
z_source         = t / 2 + 41.5 mm
```

The `1.5 mm` source clearance is a numerical separation between the virtual
source and the steel upstream face. It is not a steel-to-tile gap.

| Tile thickness | Tile front | Steel center | Steel upstream | Source Z |
| ---: | ---: | ---: | ---: | ---: |
| `4 mm` | `2 mm` | `22 mm` | `42 mm` | `43.5 mm` |
| `8 mm` | `4 mm` | `24 mm` | `44 mm` | `45.5 mm` |
| `12 mm` | `6 mm` | `26 mm` | `46 mm` | `47.5 mm` |
| `16 mm` | `8 mm` | `28 mm` | `48 mm` | `49.5 mm` |
| `20 mm` | `10 mm` | `30 mm` | `50 mm` | `51.5 mm` |
| `24 mm` | `12 mm` | `32 mm` | `52 mm` | `53.5 mm` |

### SMS-005 — No air gap and retained reflector

The steel downstream face and tile `+Z` face have zero geometric gap. The tile
nevertheless remains painted or wrapped. Represent the retained EJ-510 coating
as the same zero-thickness optical-surface proxy used by the accepted optical
baseline:

- ordered `tile -> steel` border surface at the contact;
- `polishedfrontpainted`, UNIFIED, `dielectric_dielectric`;
- repository empirical EJ-510 reflectivity curve;
- painted `tile -> world` treatment on exposed tile boundaries;
- no bare `dielectric_metal` EJ-200-to-steel boundary in the primary model.

### SMS-006 — Tile and optical baseline

Use an EJ-200 tile with fixed transverse size `100 x 100 mm`. The thickness
axis is exactly:

```text
4, 8, 12, 16, 20, 24 mm
```

Across all thicknesses and layouts, lock:

- surface preset: `polishedfrontpainted`;
- reflectivity model: empirical EJ-510 CSV;
- SiPM active volume: `2.4 x 2.4 x 0.5 mm` silicon proxy;
- dimple and bottom cavity: disabled;
- explicit grease solid: disabled;
- optical coupling: undimpled zero-gap EJ-550 proxy
  (`optical_coupling=none`);
- scintillator and optical-process properties: unchanged from the prior
  lab-optimization baseline.

The zero-gap coupling is intentionally described as an EJ-550 proxy: the
physical setup used EJ-550, while the previously optimized simulation did not
assign a confirmed grease-layer thickness. Enabling an explicit grease volume
would change the baseline and is not allowed under this preset.

### SMS-007 — Three SiPM layouts and stable sensor identity

The study-layout axis is exactly:

| Study layout | Detector layout | Face | Sensor centers in face-local coordinates | Copy numbers |
| --- | --- | --- | --- | --- |
| `back-center` | `single` | `-Z` | `(0, 0) mm` | `0` |
| `edge-center` | `single` | `+X` | `(0, 0) mm` | `0` |
| `back-four` | `back-four` | `-Z` | `(-25,-25)`, `(-25,+25)`, `(+25,-25)`, `(+25,+25) mm` | `0,1,2,3` in that order |

The `+X` choice for “on edge” is a coordinate convention. For a centered
square tile and centered source, the four lateral faces are symmetry-equivalent
in the nominal model. A later study of broken symmetry must introduce a new
layout or orientation axis rather than silently changing this convention.

All SiPM placements share one logical volume and use stable physical copy
numbers. Event output stores the aggregate count plus four fixed per-copy
fields. For single-SiPM layouts, sensor 0 carries the aggregate and sensors
1–3 remain zero. The required invariant is:

```text
sipm_detected_photons
  = sum(sipm_sensor_0_detected_photons ... sipm_sensor_3_detected_photons)
```

### SMS-008 — Scan matrix

The nominal centered scan contains:

```text
6 thicknesses x 3 SiPM layouts = 18 configurations
```

Tile thickness and SiPM layout are scientific axes. Absorber transverse size
is a validation axis, not part of the detector-optimization comparison. Event
count and seed block are execution/statistical axes.

### SMS-009 — Event-level causal model

Retain the earlier D-012 causal observables and invariants, including:

- elastic, inelastic, and capture interaction counts and derived flags;
- steel energy deposition;
- charged tile-entry counts and kinetic-energy sums split into
  electron/positron, proton, and other charged categories;
- tile energy deposition split into electron/positron, proton, other charged,
  and neutral categories;
- primary-neutron tile-entry coordinates and validity;
- generated optical, scintillation, and Cerenkov counts;
- aggregate and per-sensor SiPM counts;
- collection efficiency with `NaN` plus a validity flag when no optical photon
  was generated.

Counts, category sums, flags, optional coordinates, and per-SiPM sums must be
machine-audited before a task can be accepted.

### SMS-010 — Versioned preset and allowed variation

The general scan runner exposes:

```text
--study-preset steel-module-scan-v1
--tile-thickness-mm {4,8,12,16,20,24}
--sipm-layout {back-center,edge-center,back-four}
```

Under the preset, lock the particle, kinetic energy, beam profile/direction,
tile transverse size, optical baseline, SiPM size, dimple state, coupling
model, absorber material/thickness/position, steel-to-tile gap, and source-Z
derivation. Reject manual overrides of those values.

Allow only:

- the six accepted tile thicknesses;
- the three accepted SiPM layouts;
- `200/300/500 mm` absorber transverse sizes for convergence work;
- one explicit centered scan point per invocation;
- event count, explicit seed pair, seed-block identity, and campaign metadata.

The new preset uses run-config schema `opnovice2-run-config-v4` and event schema
`opnovice2-scan-event-v3`. The old preset remains on its historical schema and
constants.

### SMS-011 — Evidence separation

Do not merge, pool, or substitute any of the following into the new scan:

- old `realistic-neutron-v1` events at `432.58 MeV`;
- the old 50 mm-tile benchmark;
- the generated but unsubmitted old convergence pilot;
- development runs lacking the new preset, layout, commit, environment, and
  seed identity.

The old benchmark may guide an initial task-size guess only. Because kinetic
energy, tile area, maximum thickness, and SiPM multiplicity changed, the new
scan requires its own full-optical benchmark before production statistics are
chosen.

### SMS-012 — Validation ladder before production

The minimum acceptance sequence is:

1. deterministic runner-contract checks for all 18 nominal configurations and
   fail-fast checks for locked overrides;
2. compile the candidate executable and retain the absorber-disabled fixed-seed
   electron regression against the accepted `practice@50ec06d4` baseline;
3. initialize and overlap-check all 18 geometries with the `500 mm` absorber;
4. run at least one full-optical event for every geometry and audit the complete
   event schema, including per-sensor sums;
5. visually inspect representative `back-center`, `edge-center`, and
   `back-four` geometries, including the steel placement and each SiPM face;
6. run a new full-optical resource benchmark that includes the likely cost
   envelope, at minimum the `4 mm back-center`, `24 mm back-center`,
   `24 mm edge-center`, and `24 mm back-four` configurations;
7. review the measured wall time, memory, output size, interaction fraction,
   photon multiplicity, and zero-light fraction before choosing pilot blocks;
8. run a new independent statistical/convergence pilot; freeze production N
   only after bootstrap precision and absorber-size convergence are reviewed.

The accepted benchmark used `100` events in each of the four envelope
configurations. Its slowest task was `24 mm back-four` at `279 s` for 100
events, so a linear estimate places a `250`-event block at `697.5 s`
(`11.625 min`), comfortably below the one-hour task target.

The convergence pilot is now frozen as follows:

- `500 mm` absorber for all six thicknesses and all three layouts: 18 nominal
  configurations;
- `200` and `300 mm` absorber checks only at the `4` and `24 mm` thickness
  endpoints for all three layouts: 12 additional configurations;
- 30 configurations total, each split into four independent seed blocks of
  250 events;
- 1,000 events per configuration, 120 logical tasks, and 30,000 events total.

The canonical ordered selection is
`hpc/osc/configurations/steel-module-convergence-pilot-v1.tsv`. This pilot is
an independent statistical and absorber-convergence study; it does not freeze
the later production event count.

### SMS-013 — Primary analysis

For each SiPM layout, report thickness-dependent curves for:

- interaction probability and zero-light fractions;
- scintillation photons per incident neutron;
- aggregate collection efficiency;
- aggregate SiPM photons per incident neutron;
- per-sensor SiPM photons where applicable.

Compare thicknesses within the same layout, then compare layouts at the same
thickness. Production, collection, and net-response effects must remain
separate. The pilot will determine which ratios or curve contrasts can meet the
candidate precision scenarios without pathological behavior from the large
zero-response component.

The scan-specific analyzer must preserve that separation and write:

- configuration-level thickness curves and Wilson intervals for interaction
  and all three zero-light definitions;
- per-sensor response intervals;
- thickness ratios relative to `4 mm` within one layout and absorber size;
- layout ratios relative to `back-center` at one thickness and absorber size;
- `200/500` and `300/500` absorber-convergence ratios at the two thickness
  endpoints;
- production-`N` projections for 5% and 10% relative 95% CI half-width
  scenarios.

The projection model applies a fixed `1.25` safety factor and rounds upward to
complete 250-event execution blocks. The 5% and 10% projections are review
aids only. Neither precision scenario is an accepted target, and the analyzer
must not auto-approve a production event count or absorber size.

### SMS-014 — Historical pilot review and analysis-v2 gate (closed)

This section preserves the gate as defined after the v1 review. Its required
analysis was subsequently completed; SMS-015 is authoritative for the current
absorber and production boundary.

The 30-configuration convergence pilot completed all 120 logical tasks and
passed finalization, the integrated event audit, and finalized/analysis
checksum verification. Its v1 interpretation is frozen in
`docs/decisions/steel-module-convergence-pilot-review-v1.md`.

The accepted preliminary result is that `24 mm` tiles produce approximately
five times the scintillation light of `4 mm` tiles, while the net SiPM response
depends on readout geometry. At `500 mm` absorber transverse size, the 24/4 net
ratios are `0.742 [0.461, 1.303]` for `back-center`,
`2.148 [1.621, 2.860]` for `edge-center`, and
`3.424 [2.553, 4.666]` for aggregate `back-four`. The latter uses four times
the active SiPM area of a single-SiPM layout.

The v1 absorber evidence does not establish 5% equivalence: production and net
intervals for every `200/500` and `300/500` check include unity, but none lies
wholly inside `[0.95, 1.05]`. Retain `500 mm` as the conservative analysis
reference without calling it converged.

The v1 universal production-size projections were not accepted because the
limiting `back-center 8/4` net ratio is a secondary, unresolved cancellation
and was not the agreed production estimand. The gate required analysis-v2 to
use the existing audited ROOT events to:

- diagnose zero inflation, heavy tails, and seed-block stability;
- pool layout-invariant production at fixed thickness and absorber size;
- report labeled standardized-response decompositions while preserving direct
  net response as the primary empirical quantity;
- make `24/4` endpoint production and per-layout net response the primary
  sizing contrasts;
- revisit absorber equivalence with pooled production and separate 5%/10%
  review windows;
- produce contrast-specific, non-automatic event-count projections in a new
  checksummed output directory without overwriting v1.

At that checkpoint, production remained blocked until the analysis-v2 outputs
were reviewed and an explicit precision target, primary contrast set, absorber
conclusion, and event count were accepted.

The implementation gate was complete at that checkpoint: the independent v2
analyzer, synthetic infrastructure checker, and headless plotter were present.
They did not modify the v1 analyzer or launch Geant4. The real pilot still had
to be analyzed from a clean OSC checkout, checksum-verified, plotted, and
reviewed. The v2 contract fixed exactly four sizing contrasts and eight
separate projections, with no universal recommendation or automatic production
acceptance.

### SMS-015 — Analysis-v2 review and fixed-reference absorber policy

The real analysis-v2 run completed from the sealed 30,000-event pilot and
passed its core and figure checksum verification. Its accepted review is
recorded in
`docs/decisions/steel-module-analysis-v2-review-v1.md`. The analysis used
simulation commit `cfd7d974af2c5f40f7d76948172fc87ee70bfa3c` and clean analysis
commit `ad680deecc7ae6f25ff68e7a0a3b052158a21eb3`, reconciled all 30 v1
configuration estimates and three v1 endpoint ratios, and retained exactly the
four accepted primary sizing contrasts.

At the `500 mm` transverse reference, the pooled scintillation-production
`24/4` ratio is `4.836 [4.064, 5.759]`. The direct observed-net ratios are
`0.742 [0.465, 1.307]` for `back-center`,
`2.148 [1.626, 2.840]` for `edge-center`, and
`3.424 [2.546, 4.649]` for aggregate `back-four`. The corresponding maximum
leave-one-block-out shifts are `19.22%`, `7.74%`, and `5.29%`; pooled
production shifts by at most `4.01%`. The `back-center` point estimate remains
an unresolved, heavy-tail-sensitive cancellation rather than a resolved
decrease.

Analysis-v2 does not establish absorber equivalence at either the 5% or 10%
review band. In particular, pooled production for `4 mm` gives
`200/500 = 0.816 [0.667, 0.993]`, while the other pooled endpoint comparisons
remain inconclusive. The candidate-size evidence therefore cannot justify
substituting `200` or `300 mm`, but comparisons only below the reference also
cannot prove that `500 mm` has reached a large-size plateau.

For this v1 scan, accept `500 x 500 x 40 mm` as a **fixed model reference**:

- every production configuration retains this exact centered slab;
- `200/300 mm` remain pilot-only engineering diagnostics and are excluded from
  the primary production curves;
- `500 mm` must not be described as converged, infinite, or equivalent to the
  full official ePIC absorber geometry;
- every interpretation is explicitly conditional on the fixed `500 mm`
  transverse steel-slab proxy; and
- no additional absorber-convergence run is required before the v1 production
  decision.

This is a scope decision, not a statistical equivalence finding. Reopen the
absorber question if the study must predict official ePIC geometry, use a
smaller slab, or claim absorber-size-independent thickness/layout effects. A
reopened study must compare `500 mm` with a larger extent or implement the
relevant official continuous geometry; adding only more `200/300 mm` events
cannot demonstrate convergence above the current reference.

At the SMS-015 checkpoint, the absorber-policy gate was closed for v1 and
production remained blocked on an explicit precision target, staged treatment
of the heavy-tailed `back-center` response, and exact per-layout event counts.
SMS-016 resolves those statistical-design choices.

### SMS-016 — Accepted 10% precision and staged production policy

Accept a 10% event-bootstrap 95% relative-half-width target for exactly four
primary `24/4` contrasts: pooled scintillation production and direct observed
net response for `back-center`, `edge-center`, and aggregate `back-four`.
Excluding unity is not a stopping requirement, and secondary curve,
standardized-response, collection, and absorber ratios do not control sample
size.

The accepted sizing calculation is

`N_raw = ceil(N_pilot x (h_observed / 0.10)^2)`, followed by
`N_buffered = ceil(1.25 x N_raw)` and upward rounding to complete 250-event
blocks. The sealed pilot gives:

| Primary contrast | `h_observed` | `N_pilot` per endpoint | Accepted events/configuration | Blocks/configuration |
| --- | ---: | ---: | ---: | ---: |
| Pooled production, per layout stratum | `0.175253` | `3,000` total | `4,000` | `16` |
| `back-center` observed net | `0.567095` | `1,000` | `40,250` | `161` |
| `edge-center` observed net | `0.282524` | `1,000` | `10,000` | `40` |
| `back-four` observed net | `0.307109` | `1,000` | `12,000` | `48` |

The exact raw, buffered, stratum-distribution, and block-rounding arithmetic is
preserved in `docs/decisions/steel-module-analysis-v2-review-v1.md` and the
checksum-sealed `production_sizing_v2.json`.

Apply those counts to the six-thickness curve as follows:

- all six `back-center` configurations start with `4,000` new events;
- only the `4` and `24 mm back-center` primary endpoints may extend
  cumulatively through `4,000`, `10,000`, `20,000`, and `40,250` events;
- all six `edge-center` configurations receive `10,000` events; and
- all six `back-four` configurations receive `12,000` events.

The existing 30,000-event pilot is not pooled into production. Every
production block uses a fresh seed and immutable stage manifest. At the
maximum checkpoint the new-event accounting is

`4 x 4,000 + 2 x 40,250 + 6 x 10,000 + 6 x 12,000 = 228,500 events`,

or

`4 x 16 + 2 x 161 + 6 x 40 + 6 x 48 = 914` complete 250-event blocks.

Because the final endpoint samples are unequal across layouts, pooled
production retains equal `1/3` layout-stratum weights: bootstrap within each
layout at its available sample size, then average the three layout means.
Do not concatenate all events into an event-count-weighted mean that would
silently overweight the staged `back-center` sample. This equals the v2 pilot
calculation when all three strata have the same event count.

For the two `back-center` endpoints, the cumulative stages add `8,000`,
`12,000`, `20,000`, and `40,500` events across the endpoint pair. Including
the fixed allocations, the corresponding whole-production ceilings are
`156,000/624`, `168,000/672`, `188,000/752`, and `228,500/914`
events/blocks.

At each checkpoint, require finalized checksums, event/seed audit, cumulative
analysis, and review of relative half-width, maximum leave-one-block-out
shift, zero/positive counts, top-tail shares, maximum event, and CI-width
scaling. Stop successfully only when the `back-center 24/4` relative
half-width and maximum block-omission shift are both `<= 10%`. Continue only
after explicit review when the target is unmet, the block shift is `<= 20%`,
and the interval is narrowing. Pause for method review if those diagnostics
deteriorate. `40,250` events/configuration is a hard ceiling, not permission
for automatic extension.

For `BC-S1`, compare interval-width trend against the sealed 1,000-event pilot
as a diagnostic only, without pooling pilot events. Later stages compare to
the immediately preceding cumulative production checkpoint.

The fixed pooled-production, `edge-center`, and `back-four` allocations must
also be checked against the accepted 10% target. If any misses it, do not add
events automatically; reopen that contrast-specific sizing decision.

This cumulative repeated-look policy is a pragmatic precision design, not a
formally adjusted sequential-confidence procedure. Stopping must never depend
on the effect direction, crossing unity, or excluding unity. The policy
authorizes production-infrastructure preparation; actual submission remains
blocked until immutable stage generation, fresh-seed identity, cumulative
audit/analysis, and non-overwrite behavior are implemented and validated.

## Current engineering state

The runner contract, three detector-layout identities, and independent
`steel-module-campaign-v1` manifest/task/result chain are implemented. The
accepted frozen source is commit
`88f15eace17137475310913d585a96908a493c72` with Geant4 `11.4.2`. The
absorber-disabled fixed-seed electron differential regression against
`practice@50ec06d4` passed.

The user visually accepted all three layouts at `16 mm`. The formal 18-task
OSC geometry smoke, campaign `sm-v1-geometry-smoke-d77d801b947c`, completed and
passed the integrated event audit and finalized checksum verification.

The four-configuration, 100-event-per-task benchmark, campaign
`sm-v1-benchmark-378e088c618d`, also completed and passed finalization. Its
recorded envelope was:

| Configuration | Wall time | MaxRSS | Interaction fraction | Generated optical / event | SiPM / event | Collection | SiPM zero fraction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `4 mm back-center` | `23 s` | `173416 K` | `0.340` | `2790.30` | `17.54` | `0.00629` | `0.890` |
| `24 mm back-center` | `217 s` | `227640 K` | `0.390` | `41415.77` | `186.20` | `0.00450` | `0.790` |
| `24 mm edge-center` | `222 s` | `223116 K` | `0.370` | `42595.82` | `184.17` | `0.00432` | `0.740` |
| `24 mm back-four` | `279 s` | `232324 K` | `0.320` | `46074.89` | `627.94` | `0.01363` | `0.770` |

These results support the accepted `250 events x 4 blocks` convergence pilot.
The resulting campaign used source commit
`cfd7d974af2c5f40f7d76948172fc87ee70bfa3c`, ran as Slurm array `50477293`,
and produced 30,000 accepted events. All 120 logical tasks were selected by the
finalizer; the integrated event audit, accepted-statistical-evidence flag, and
finalized/analysis checksum checks passed.

The historical v1 scientific review is recorded in
`docs/decisions/steel-module-convergence-pilot-review-v1.md`. The completed
analysis-v2 interpretation and accepted absorber and staged-production
policies are recorded in
`docs/decisions/steel-module-analysis-v2-review-v1.md`. Together they support
a layout-dependent thick-tile conclusion while preserving the fixed-proxy and
heavy-tail caveats.

No geometry, source-model, absorber, or statistical-design input remains open.
The remaining gate is implementation and validation of the accepted staged
production workflow before its first Slurm submission.

## Interactive geometry review

The scan-specific visualizer prepares all three accepted layouts by default:

```bash
cd test/OpNovice2
cmake -S . -B build-gui -DWITH_GEANT4_UIVIS=ON
cmake --build build-gui -j4
python3 visualize_steel_module_scan_geometry.py --tile-thickness-mm 16
```

Each layout receives its own exact macro, run config, visualization metadata,
and README. The GUI sessions open in `back-center`, `edge-center`,
`back-four` order; close one session to open the next. The camera first
renders a close SiPM detail and finishes on a wider steel overview. This is a
human sanity check only and leaves `/run/beamOn 0`.

Use one or more repeated `--sipm-layout` options to prepare a subset, or
`--prepare-only` to retain the macros without opening Qt.

## Invariants

- `1 GeV` kinetic energy is authoritative for this preset.
- The source is centered, point-like, normal incidence, and pencil-like.
- The tile is `100 x 100 mm`; only the six accepted thicknesses are valid.
- The layout axis contains exactly `back-center`, `edge-center`, and
  `back-four` with the recorded face/position/copy-number convention.
- The steel is 40 mm SAE 304 with zero tile air gap and retained paint/wrap.
- The optical baseline is polished-front-painted EJ-510 plus the undimpled
  zero-gap EJ-550 coupling proxy.
- Aggregate SiPM count equals the sum of the four fixed per-sensor fields.
- Old 432.58 MeV data never enter this scan's statistical evidence.
- Production submission starts only after the accepted pilot, analysis-v2,
  absorber, and SMS-016 statistical decisions are embodied in a validated
  immutable staged-production workflow.

## Requirement sources retained with scope

`SteelModuleScan.md` specifies the `100 x 100 mm` tile, six thicknesses, and
three SiPM arrangements. Its original momentum phrase is superseded for this
preset by the later professor instruction relayed on 2026-07-15 to set neutron
kinetic energy to `1 GeV`.

Professor clarification for source position:

> shoot it as a gun, the location shouldn't matter now - just shoot at the center for now

Previously confirmed optical-boundary clarification remains in force:

> the tiles will still be painted/wrapped, just mean there is no air-gap between the tiles and steel.
