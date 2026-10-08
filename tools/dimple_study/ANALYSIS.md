# Exploratory analysis of the completed local line scan

The measurement definitions and independent geometry comparison were fixed in
`LINE_SCAN.md`. The bootstrap choices below were specified on 2026-10-08 **after
the scan and after viewing its point estimates**. They are an exploratory
analysis, not a preregistered confirmatory test.

## Evidence and observations

Use only `outputs/dimple-study/line-v1`, sealed by manifest SHA256
`f1c2b13566103515ca569764de34142a5c5ca74802fc936f0ff38947f8206405`.
Verify all 52 accepted tasks and original ROOT/CSV/log/RNG/input identities.
The observation is one incident 1 MeV electron, identified by `(task,event_id)`.
Combine each configuration's two independent 100-event blocks into 200 events;
retain all zeros and keep the block labels. All 5,200 events are included.

The primary quantity is mean D per incident electron. Auxiliary quantities are
mean legacy G, `sum(D)/sum(G)`, all optical births, optical-parent birth fraction,
and e± energy deposited in the tile. e± energy includes secondary e−/e+ tracks;
it is not an energy ledger for the primary electron alone. Total tile energy
includes other particles, including optical-photon absorption.

## Uncertainty

- Use 100,000 nonparametric bootstrap replicates per configuration, with fixed
  analysis seed `2026100801`. Independently sample 200 whole events with
  replacement in each cell. Apply the same event indices to D, G, births and
  energy so within-event covariance is retained.
- Allocate independent NumPy `SeedSequence` children to the 26 cells (flat then
  dimple, increasing x). No flat/dimple pairing by event number or seed.
- Configuration means, ratios, relative changes, symmetry checks and block
  diagnostics have descriptive pointwise 95% percentile intervals.
- For the **13 absolute mean-D contrasts only**, also report percentile bounds
  at `0.05/(2*13)` and `1−0.05/(2*13)`. These are 99.6153846% individual
  intervals targeting nominal family-wise 95% by Bonferroni. Bootstrap coverage
  remains approximate; the 100,000-replicate tails each contain about 192 draws.
- Preserve undefined ratios. A zero point denominator becomes null. Any
  nonfinite ratio replicate makes that interval unavailable, and its count is
  reported; do not drop invalid replicates or redraw a more favorable sample.
- Two block means are a stability diagnostic, not two independent observations
  replacing the 200 electrons. Use separate 20,000-replicate whole-event
  bootstraps of each 100-event block for the block-1 minus block-0 mean-D
  difference. Those 26 intervals are descriptive and unadjusted.
- Check +x minus −x response separately for each geometry. Do not average
  symmetric positions before analysis. Those 12 checks are also descriptive;
  a single 95% discrepancy is not proof of an asymmetry or simulation defect.

Save the replicate arrays and source inventory so interval endpoints can be
checked without another simulation or bootstrap run.

## Interpretation and figures

Show collected photons, generated photons and legacy collection ratio
separately, with a full-line view and a near-cavity view using true numeric x
spacing. Connecting segments are visual guides only, not a fitted response
between the sparse sampled positions. Mark the circular opening at x=±3 mm.

The identity

```text
meanD_dimple / meanD_flat
  = (meanG_dimple / meanG_flat) × (legacyRatio_dimple / legacyRatio_flat)
```

is an arithmetic decomposition, not a causal attribution. The cavity changes
the material encountered by electrons and the light-emission distribution as
well as optical transport. Its straight, undeflected incident material length
is `5−sqrt(9−x²)` mm for |x|<3, otherwise 5 mm; this is not the actual scattered
electron track length. The source records have no full optical fate/path ledger.

Results describe this y=0 line, 6×6 mm external SiPM, bare-polished/no-grease
model and finite-absorption air. They do not establish full-tile uniformity,
PDE/electronics response, a general dimple design rule, or the improvement from
2×2 to 6×6 mm under an otherwise identical optical protocol.

## Reproduce and validate

The recorded analysis used Python 3.14.4, NumPy 2.4.6 and uproot 5.7.5;
figures used Matplotlib 3.11.0. Use new output paths to preserve earlier work.

```sh
python -B tools/dimple_study/analyze_line.py \
  --batch outputs/dimple-study/line-v1 \
  --output outputs/dimple-study/line-analysis-new \
  --manifest-sha256 f1c2b13566103515ca569764de34142a5c5ca74802fc936f0ff38947f8206405
python -B tools/dimple_study/validate_line_analysis.py \
  --batch outputs/dimple-study/line-v1 \
  --analysis outputs/dimple-study/line-analysis-new \
  --analysis-script tools/dimple_study/analyze_line.py \
  --manifest-sha256 f1c2b13566103515ca569764de34142a5c5ca74802fc936f0ff38947f8206405 \
  --output outputs/dimple-study/line-analysis-new/independent-validation.json
```

The completed [result package](results/line-v1/README.md) includes the exact
tables, extracted event rows, final PNG/SVG figures and independent validation.
The validator recomputes estimates from raw ROOT and intervals from stored
bootstrap draws; it does not rerun the production analyzer or resample events.
