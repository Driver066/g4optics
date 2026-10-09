# Dimple study — paused handoff, 2026-10-09

**Status: paused by the project owner; lowest current research priority.**
The completed electron result remains preserved. No lab-like dimple model,
new lab-study branch, beta campaign, or grease/paint modification was started.
Resume only after a new instruction; this record does not authorize a scan.

## Priority and upstream context

The owner has deferred this study in favor of the new upstream work. The open
issues inspected on 2026-10-09 are:

- [#7 — GEANT validation of combined tiles](https://github.com/jdbrice/g4optics/issues/7):
  compare monolithic 16/24 mm tiles with 8+8 / 8+16 mm assemblies and a thin
  grease interface (the issue suggests 0.1 mm; the refractive index needs checking).
- [#8 — Light uniformity for 4 SiPm](https://github.com/jdbrice/g4optics/issues/8):
  compare four back sensors with two side sensors and one back-center sensor.
- [#9 — Dimple effect](https://github.com/jdbrice/g4optics/issues/9): a future
  **24 mm-thick**, back-center-SiPM study including air dimple, larger SiPM,
  compensating front hemisphere, and square recess with restored material.

No order between #7 and #8 has been selected here. The existing **5 mm-thick**
diagnostic line scan does not fulfill #9. The 0.1 mm suggestion in #7 concerns
the interface between stacked tiles; it is not a decision about dimple grease.

## Frozen completed result

Published source/results/reproduction snapshot:
`45ed24bbe697606afa88184b689905aa732d0e73` on `codex/dimple-study`.
Later documentation commits do not replace this scientific reference.

- Starting base: `404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5`.
- Geometry implementation: `52fc522fe1391e6ae0171d3e3f5c9a69b465b7cb`.
- Accepted simulation source and passive diagnostics:
  `5d6c50d37e33178e27710df5cc13e0127593acbe`.
- Frozen scan tooling: `192b9a09e007c5fa5453a6390d369732a9be0082`.
- Analysis: `8f9e7142fad9627031599adb4146dbc9e8de4d96`.

The model is a single 100 × 100 × 5 mm tile, with either a flat bottom or a
central hemispherical air cavity of radius/depth 3 mm. Both cases use the same
external 6 × 6 × 0.5 mm SiPM, centered at (0, 0, −2.75) mm, with receiving plane
z = −2.5 mm. Interfaces are bare polished, with no grease. The inherited World
optical absorption length is **100 mm**, not infinite transparency.

One 1 MeV electron starts at (x, 0, 4) mm along −Z. The x grid is
−40, −10, −5, −3.2, −2.8, −1.5, 0, 1.5, 2.8, 3.2, 5, 10, 40 mm.
Two geometries × 13 positions × two independent 100-event seed blocks gave
**52 accepted tasks / 5,200 events**, completed on 2026-10-08. No failed task
was replaced and no event was removed.

The dimple's mean collected D is lower at all 13 sampled positions, by
48.62%–69.28%. At the center, D is 2499.2 → 767.8 photons/electron, and legacy
ΣD/ΣG is 26.37% → 10.44%. See [full results](results/line-v1/README.en.md) and
[analysis definitions](ANALYSIS.md) for estimates and uncertainty.

D counts entry into any face of the SiPM proxy, without PDE/electronics. G is
the legacy denominator, excluding optical-parent Scintillation births; the
baseline optical process registration is retained. Do not import the rejected
old W08 closure assumption or disable opticalphoton Scintillation to reproduce
this result. The analysis is exploratory and post-scan. It does not establish
a particular loss mechanism, the benefit of a larger SiPM, full-tile
uniformity, or the behavior of a painted/grease-coupled sample.

## Public archive and reproduction

- [Discussion #4](https://github.com/Driver066/g4optics/discussions/4) retains
  the geometry review and the
  [published result update](https://github.com/Driver066/g4optics/discussions/4#discussioncomment-18810241).
- [Reproduction instructions](REPRODUCE.md): replot, CSV replay, original ROOT
  reanalysis, and a source rebuild followed by qualification/full simulation.
- [Public result bundle](results/line-v1/): estimates, 5,200 event rows, PNG/SVG,
  manifests, validation and `raw-campaign.tar.gz`.
- Raw archive: **2,842,388 bytes; 406 files; all 52 original ROOT files**, with
  macros, summaries, logs, RNG states, receipts and source/runtime records.
  SHA256: `c00ab31ac475846a508d0f6e8e00ac2b849355d9a5c2ce2c9bc9a06f5154f7dc`.
- Campaign manifest SHA256:
  `f1c2b13566103515ca569764de34142a5c5ca74802fc936f0ff38947f8206405`.
- Accepted executable SHA256:
  `2996e0eeb3818d3f1801e9530fa2f38e16c297223160c2c6a5e8a791c8ef7bb6`.

Use an independent checkout for historical reproduction:

```sh
git clone --branch codex/dimple-study https://github.com/Driver066/g4optics.git g4optics-dimple-replay
cd g4optics-dimple-replay
git checkout --detach 45ed24bbe697606afa88184b689905aa732d0e73
```

The published [reproduction validation](reproduction-validation.json) records
complete CSV and ROOT reanalysis and a fresh source build with ten qualification
events on linux/arm64. It does **not** record a second complete 5,200-event
simulation, fresh dataset downloads or an amd64 execution. Cross-platform
bitwise equality is not promised. Old source-hash checks must remain strict;
future C++ changes belong to a separately identified model and campaign.

Additional local evidence remains under `outputs/dimple-study/`: `geometry-v2`,
`geometry-views-v3`, `optical-v1`, `line-v1`, and `line-analysis-v1` (final figures
in `plots-v2`). These ignored directories, including the large bootstrap arrays,
were retained in place, not removed or implicitly backed up by Git. The public
archive supports the documented reconstruction without those local directories.
No new simulation or environment installation was performed for this handoff.

## Cross-repository lab evidence — read before resuming

The later calibration evidence is in the separate
[`Driver066/g4optics-nhcal-paper`](https://github.com/Driver066/g4optics-nhcal-paper)
repository. Do not infer the current calibration from old 55 mrad examples in
the simulation repository alone.

- [RED-03D, pinned at 3f9fa769](https://github.com/Driver066/g4optics-nhcal-paper/blob/3f9fa769d794620145ad0ca16551bdd6cee9f1eb/figures/red-03d-divergence-grid-5to305/README.md):
  the 2026-07-20 grid covers 5–305 mrad by 10 mrad, two 50 × 50 mm tiles of
  thickness 4/16 mm, 15 positions each, two 2,500-event blocks per point
  (4.65 million primaries). `inputs/summary.json`, `curve.csv`,
  `assembly_contract.json`, `provenance.json` and `MANIFEST.tsv` accompany it.
- The supported result is a broad **155–215 mrad trough** (NRMSE 6.53%–7.04%),
  versus 10.76% at 55 mrad. The pooled grid minimum is 195 mrad (6.5281%), but
  the two blocks select 195/165 mrad and the two thicknesses select 195/175.
  **195 mrad is not a unique measured optimum.** Blue/red block curves are
  stability checks, not confidence intervals. The metric compares spatial
  shape after fitting an amplitude per tile; it does not calibrate absolute yield.
- [Latest RR-013 decision, pinned at dace00ce](https://github.com/Driver066/g4optics-nhcal-paper/blob/dace00ce3d4fac16b5179447bf299fd77d6ad4ac/evidence/operations/rr013-divergence-fit-route-and-195-checkpoint-2026-07-20.zh.md):
  the top section supersedes older commands below it. No additional divergence
  points are authorized; a local parabola fit of existing data remains
  conditional on professor confirmation. Do not execute historical RR-013B
  scan commands or adopt the diagnostic ~182 mrad vertex as a completed result.
- `figures/red-04c-195mrad-allfour-checkpoint/README.md` retains the all-four
  comparison: 195 mrad improves the two 50 mm shape comparisons, but does not
  improve both 115 mm cases. The historical large-tile 55 mrad sample is coarse.

That historical source uses `sr90-spectrum` / `sr90_allowed_beta_v1`
(Sr-90/Y-90 continuous GPS spectrum), transverse Gaussian σx = σy = 5 mm,
and GPS `beam2d` angular widths. It is not the older `sr90-empirical` option.
It uses a 2.4 × 2.4 × 0.5 mm back-center SiPM, `polishedfrontpainted` plus
digitized EJ-510 reflectivity, **`optical_coupling=none`, `grease_enabled=false`,
and no dimple**. The zero-gap coupling proxy is not an explicit grease layer.
The source-to-contact distance is 38 mm: source z = 30 mm for 16 mm tiles and
z = 36 mm for 4 mm tiles. Do not blindly reuse z = 30 mm for a new thickness.
This calibration did not validate a dimple-plus-grease assembly.

## Unresolved model decisions and restart gate

No physical grease arrangement has been chosen: a thin pad outside an air
cavity versus filling the cavity, layer thickness, and paint coverage of the
cavity wall and entire 6 × 6 mm coupling window remain unresolved. A proposed
0.1 mm dimple pad was only an example, not a measured lab dimension or decision.
The current `external` geometry intentionally rejects grease. A global painted
surface also affects the cavity wall unless surfaces are explicitly separated.

On a future authorized restart:

1. Re-read upstream #9 and agree the target thickness, physical assembly and
   comparison matrix; distinguish it from the preserved 5 mm diagnostic case.
2. Inspect both repositories and their current branches/dirty files before edits.
   Keep old results, source hashes and output directories intact. Select a
   separate branch for the newly defined model; no lab-study branch exists yet.
3. Resolve grease and paint assumptions explicitly. Retain an opt-in path for
   new geometry/physics, and check the old electron observables and final RNG
   state with matched seeds where the old mode is expected to remain unchanged.
4. Inspect actual geometry renders, overlap checks and a small source/counting
   run before registering a full campaign. A beta beam requires new source
   audits; fixed 1 MeV primary checks are not suitable unchanged.
5. Freeze the new source/build/environment, seeds, configuration and outputs
   independently, then obtain/confirm the intended scan scope before execution.

No repository, worktree, ignored output directory, Docker image or dataset was
deleted as part of pausing this study.
