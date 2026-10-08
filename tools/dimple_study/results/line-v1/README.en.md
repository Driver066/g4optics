# Exploratory dimple line scan: external 6 × 6 mm SiPM

Results recorded on 2026-10-08. [中文说明](README.md) ·
[Reproduction guide](../../REPRODUCE.md) · [Analysis definitions](../../ANALYSIS.md)

**The dimple model collected fewer photons than the flat control at all 13
sampled positions on the y = 0 line.** The estimated reduction in mean collected
photons per incident electron ranged from **48.62% to 69.28%**. This conclusion
applies to the specified model and sampled positions below.

## Model and sample

- One 100 × 100 × 5 mm scintillator tile, with or without a bottom-centred
  hemispherical dimple of radius and depth 3 mm.
- The same external 6 × 6 × 0.5 mm SiPM proxy in both geometries: centre
  (0, 0, −2.75) mm, receiving face at z = −2.5 mm. Its square footprint covers
  the complete circular opening in projection.
- Bare polished surfaces, an air-filled cavity, and no optical grease. The
  World retains the existing model's **100 mm optical absorption length**.
- One 1 MeV electron per event, starting at (x, 0, 4) mm in direction −Z.
- x = −40, −10, −5, −3.2, −2.8, −1.5, 0, 1.5, 2.8, 3.2, 5, 10, 40 mm.
  Each geometry/position has two independently seeded 100-event blocks:
  **200 events per configuration, 52 tasks, 5,200 events total**. All events
  were retained; this batch contained no zero-D or zero-G events.

The recorded simulation used Geant4 11.4.2, Serial, linux/arm64, with simulation
source `5d6c50d37e33178e27710df5cc13e0127593acbe`. The two geometries are independent
samples, not paired by event number or seed.

## Response and centre-point comparison

![Mean collected photons, legacy generated photons, and legacy collection ratio](response.png)

D counts photons entering the SiPM proxy through any face; it includes no PDE
or electronics response. G retains the legacy production counter and excludes
Scintillation births from optical-photon parents. The reported legacy ratio
is **ΣD/ΣG**, not the mean of event-level ratios or a complete photon-birth
efficiency. Optical-parent births account for approximately 0.47%–0.54% of all
optical births across the configurations; their counts are retained separately.

At **x = 0 mm**, brackets show descriptive pointwise 95% bootstrap intervals.
Relative change means dimple/flat − 1.

| Quantity | Flat | Dimple | Relative change [95% interval] |
|---|---:|---:|---:|
| Mean D, photons/electron | 2499.2 [2455.5, 2540.5] | 767.8 [721.7, 814.4] | −69.28% [−71.20%, −67.33%] |
| Mean legacy G, photons/electron | 9478.3 [9372.9, 9551.5] | 7355.2 [7006.2, 7696.0] | −22.40% [−26.15%, −18.72%] |
| Legacy ΣD/ΣG | 26.37% [26.03%, 26.71%] | 10.44% [10.12%, 10.79%] | −60.41% [−61.72%, −58.99%] |
| Mean e± tile energy deposition, MeV/electron | 0.99059 [0.97955, 0.99806] | 0.76969 [0.73260, 0.80589] | −22.30% [−26.11%, −18.57%] |

The centre-point absolute mean-D difference is **−1731.38 photons/electron**.
Its adjusted interval is **[−1822.10, −1636.53]** photons/electron.

![Dimple-minus-flat contrasts and their intervals](contrasts.png)

## Uncertainty and interpretation

The analysis uses 100,000 whole-event bootstrap replicates per configuration,
with fixed analysis seed `2026100801`. Resampling preserves the within-event
covariance of D, G, births, and energy. Only the 13 absolute mean-D contrasts
also receive Bonferroni-adjusted individual **99.6153846%** intervals, targeting
nominal family-wise 95%; all 13 are below zero. Other intervals are descriptive
pointwise 95% intervals. Bootstrap coverage is approximate, and these analysis
choices were made **after the scan and inspection of its point estimates**:
this is exploratory analysis, not a preregistered confirmatory test.

The centre loses both electron/positron energy deposition and legacy light
production. The dimple leaves 2 mm of material along the undeflected central
incident line, compared with 5 mm for the flat control; this is not the actual
scattered electron path length. Deposited energy includes secondary e−/e+.
The simultaneous reduction in ΣD/ΣG shows that the D difference is not described
by G alone. Decomposing the D ratio into a G ratio and a legacy collection-ratio
ratio is an arithmetic identity, **not a separation of causal contributions**.

The simulation records used here do not contain a complete optical path,
boundary-interaction, or terminal-fate ledger. They do not establish which
specific optical loss mechanism causes the difference. Nor does this scan
quantify the benefit of enlarging the SiPM: an otherwise identical 2 × 2 mm
external-SiPM optical comparison has not been run. The results do not establish
a general dimple design rule, painted-sample performance, full-tile uniformity,
or a tile-area average.

![Energy deposition and independent seed-block means](diagnostics.png)

The two block means are a diagnostic; the inferential sample remains 200
individual electrons per configuration. Descriptive block-difference and
left/right-symmetry intervals include zero throughout, which is not proof of
equivalence or sufficient precision. Figures use actual nonuniform x positions
except the explicitly labelled categorical contrast panel; connecting lines
only guide the eye.

## Evidence and reproduction

- [All estimates and intervals](result.json), [configuration table](configuration-summary.csv),
  [contrasts](contrasts.csv), and [5,200 extracted event rows](events.csv).
- [Campaign manifest](campaign-manifest.json), [input hashes](input-inventory.json),
  and [plot source/hash record](plot-manifest.json). Each PNG has a same-name SVG.
- [Independent validation](independent-validation.json) checked the 52 original
  ROOT inputs, extracted events, estimates, and interval arithmetic from saved
  bootstrap draws: 22,323 checks passed, with maximum numerical difference
  1.82 × 10⁻¹². It did not regenerate bootstrap samples or validate the physics model.
- [Reproduction guide](../../REPRODUCE.md) describes the input and environment
  requirements and the distinction between replotting, reanalysis, and a new
  simulation. The archived validation documents the recorded local run; it
  does not establish a successful fresh-environment replay elsewhere.

Replot the published estimates without ROOT files or new resampling, using a
new output directory:

```sh
python -B tools/dimple_study/plot_line.py \
  --result tools/dimple_study/results/line-v1/result.json \
  --output outputs/dimple-study/replayed-line-figures
```

Original ROOT files, macros, logs, final RNG states, receipts, and saved
bootstrap arrays are separate from the extracted event table. Consult the
reproduction guide for their availability and the applicable validation scope.
