# Steel Module Analysis-v2 Review, Absorber, and Production Policy

Status: analysis-v2 evidence reviewed; fixed-reference absorber policy and
10% staged production policy accepted; execution infrastructure pending

Study preset: `steel-module-scan-v1`

Reviewed: 2026-07-17

## Decision summary

The checksum-sealed analysis-v2 output confirms that the competition between
greater neutron-induced light production and poorer optical collection is
layout-dependent. At the fixed `500 x 500 x 40 mm` steel-slab proxy, a `24 mm`
tile produces approximately `4.84` times the scintillation light of a `4 mm`
tile. The observed aggregate SiPM response shows no resolved gain for
`back-center`, but it increases for `edge-center` and aggregate `back-four`.

The absorber study does not establish transverse-size equivalence at either
the 5% or 10% review band. For this v1 study, `500 mm` is therefore accepted as
a **fixed model reference**, not as a data-proven converged or infinite
absorber. No additional absorber-convergence simulation is required before the
v1 production decision, provided every interpretation remains explicitly
conditional on this fixed slab proxy.

This decision closes the v1 absorber-policy and statistical-design gates. The
accepted target is a 10% relative half-width for the bootstrap 95% interval of
each of the four primary `24/4` contrasts. The heavy-tailed `back-center`
endpoint configurations use predeclared cumulative checkpoints rather than a
single unconditional submission. The policy authorizes preparation of the
production infrastructure; Slurm submission remains blocked until that
infrastructure preserves the fresh-seed, immutable-stage, cumulative-audit,
and non-overwrite requirements below and passes its dry-run validation.

## Evidence identity

| Item | Accepted value |
| --- | --- |
| Campaign | `sm-v1-convergence-pilot-4e9ef8618948` |
| Campaign directory | `steel-module-convergence-pilot-cfd7d974` |
| Simulation commit | `cfd7d974af2c5f40f7d76948172fc87ee70bfa3c` |
| Analysis commit | `ad680deecc7ae6f25ff68e7a0a3b052158a21eb3` |
| Analysis schema | `steel-module-analysis-v2-config-v1` |
| Sample | 30 configurations x 4 blocks x 250 events = 30,000 events |
| Bootstrap | 10,000 event resamples; seed `20260715` |
| Accepted evidence | `true` |
| v1 reconciliation | `passed`; 30 configurations and 3 endpoint ratios |
| Core outputs | `finalized/analysis-v2`; checksum verified |
| Figures | `finalized/analysis-v2-figures`; 5 PNG + 5 PDF; checksum verified |

The finalized campaign, v1 analysis, analysis-v2, figure manifests, and all 120
audited ROOT hashes were reverified after the campaign was copied locally. The
analysis-v2 run used a clean analysis checkout and recorded its analyzer hash,
Python, NumPy, and uproot versions. The v2 outputs are an analysis of the sealed
pilot events; they do not add Geant4 events or alter the v1 analysis.

## Primary endpoint evidence

The four accepted primary contrasts are:

| Primary contrast, 24/4 | Ratio | Bootstrap 95% interval | Max block-omission shift |
| --- | ---: | ---: | ---: |
| Pooled scintillation production | `4.836` | `[4.064, 5.759]` | `4.01%` |
| Observed net, `back-center` | `0.742` | `[0.465, 1.307]` | `19.22%` |
| Observed net, `edge-center` | `2.148` | `[1.626, 2.840]` | `7.74%` |
| Observed net, aggregate `back-four` | `3.424` | `[2.546, 4.649]` | `5.29%` |

The pooled production interval and the `edge-center` and `back-four` net
intervals exclude unity. The `back-center` interval does not; its point
estimate must not be described as a resolved decrease. The corresponding
standardized-response endpoint ratios are `0.773`, `2.056`, and `3.439`, so
pooling the layout-invariant production changes no endpoint interpretation.
Standardized response remains a model-based secondary diagnostic rather than
a replacement for direct observed net response.

The production increase is present across the full fixed-500-mm curve. Pooled
scintillation production relative to `4 mm` is `1.670`, `2.524`, `3.208`,
`3.881`, and `4.836` at `8`, `12`, `16`, `20`, and `24 mm`; every adjacent
thickness interval is above unity. Every thicker point also has an observed
net interval above unity for `edge-center` and `back-four`, while no
`back-center` thickness ratio excludes unity.

`back-four` uses four sensors and four times the nominal active area. Its
aggregate response may be compared across thickness within that layout, but it
must not be presented as a sensor-area-independent efficiency improvement over
a single-SiPM layout.

## Zero response, heavy tails, and block stability

For the 18 nominal `500 mm` configurations, the recorded primary-neutron
interaction fraction is `0.322-0.374`, while the SiPM-zero fraction is
`0.751-0.819`. The high zero fraction is primarily upstream of optical
collection:

- `P(generated > 0 | recorded interaction)` is `0.507-0.643`;
- `P(SiPM > 0 | generated > 0)` is `0.953-1.000`.

Once an event produces optical photons, at least one photon almost always
enters a SiPM proxy. The unstable quantity is the photon magnitude of a small
responding subset, not the zero-event occurrence rate.

The most extreme nominal distribution is `4 mm back-center`: `79.6%` of events
have zero SiPM response, the largest 1% of events carry `58.7%` of the total
response, and the largest 5% carry `84.6%`. This heavy tail explains the wide
`back-center` interval and its `19.22%` primary-ratio block sensitivity. The
pooled production, `edge-center`, and `back-four` primary ratios all remain
inside the 10% block-review line.

Block flags are diagnostics only. Stable zero fractions do not imply stable
mean photon response, and the `1/sqrt(N)` sizing extrapolation is not a
finite-sample guarantee for a rare-shower tail.

## Absorber evidence

Analysis-v2 pools production across the three readout layouts at fixed tile
thickness and absorber size. The endpoint comparisons against the `500 mm`
reference are:

| Thickness | Candidate/500 pooled production | Bootstrap 95% interval | 5% status | 10% status |
| ---: | ---: | ---: | --- | --- |
| `4 mm` | `200/500 = 0.816` | `[0.667, 0.993]` | difference detected, not equivalent | difference detected, not equivalent |
| `4 mm` | `300/500 = 0.849` | `[0.699, 1.036]` | inconclusive | inconclusive |
| `24 mm` | `200/500 = 0.998` | `[0.857, 1.160]` | inconclusive | inconclusive |
| `24 mm` | `300/500 = 0.910` | `[0.780, 1.063]` | inconclusive | inconclusive |

None of the 12 per-layout observed-net candidate/reference intervals
establishes equivalence at either band. The one nominally detected production
difference is an unadjusted pointwise comparison in a heavy-tailed study; it is
a review signal, not a stand-alone geometry claim. Generated-optical and
scintillation rows are identical in this sample and are not independent
confirmations.

Descriptive endpoint point ratios also show why the absorber cannot be treated
as a harmless common-mode factor:

| Absorber transverse size | Pooled production 24/4 | `back-center` net 24/4 | `edge-center` net 24/4 | `back-four` net 24/4 |
| ---: | ---: | ---: | ---: | ---: |
| `200 mm` | `5.91` | `1.61` | `2.60` | `3.79` |
| `300 mm` | `5.18` | `1.71` | `2.31` | `3.54` |
| `500 mm` | `4.84` | `0.742` | `2.15` | `3.42` |

The `200` and `300 mm` values above are descriptive ratios of endpoint point
estimates, not additional primary contrasts with accepted ratio intervals.
They are especially unstable for `back-center`. They nevertheless rule out an
unqualified claim that the fixed-500-mm result is absorber-size-independent.

## Accepted absorber policy

The v1 study adopts the following policy:

1. Every production configuration uses the existing centered
   `500 x 500 x 40 mm` steel slab, with the already accepted zero gap and
   painted/wrapped tile boundary model.
2. The `500 mm` transverse size is part of the frozen study definition. It is
   not labeled "converged," "infinite," or equivalent to the full ePIC
   absorber geometry.
3. The `200` and `300 mm` pilot configurations remain immutable engineering
   diagnostics. They are excluded from the primary six-thickness production
   curves and cannot replace the `500 mm` reference.
4. Every result intended for interpretation or external communication is
   scoped as applying **for the fixed 500 mm transverse steel-slab proxy**.
5. No additional absorber-convergence run is required before the v1 production
   decision. This is a scope decision, not an equivalence finding.
6. Reopen the absorber question only if the study must:
   - predict the official ePIC module rather than the fixed proxy;
   - use an absorber smaller than `500 mm`; or
   - claim absorber-size-independent thickness or layout effects.
7. A reopened convergence study must compare `500 mm` against a larger
   transverse extent or implement the relevant official continuous geometry.
   Merely accumulating more `200/300 mm` events cannot establish that
   `500 mm` has reached a large-size plateau.

Replacing the local slab with official detector geometry would define a new
model baseline and require proportionate geometry validation, regression,
benchmark, and statistical-pilot review. It is not silently folded into this
v1 production campaign.

## Accepted 10% precision target and sizing derivation

The production target applies to four and only four primary `24/4` contrasts:
pooled scintillation production and direct observed net response for each of
the three SiPM layouts. For each contrast, the target is

\[
h = \frac{U-L}{2\hat R} \leq 0.10,
\]

where `L` and `U` are the event-bootstrap 95% interval bounds and `R-hat` is
the point ratio. Reaching this precision target does not require the interval
to exclude unity and is not an effect-direction stopping rule. Secondary
curve ratios, standardized response, collection, and absorber comparisons do
not control production sizing.

Analysis-v2 projected the required sample from the sealed pilot using

\[
N_{raw}=\left\lceil N_{pilot}\left(\frac{h_{observed}}{0.10}\right)^2\right\rceil,
\qquad
N_{buffered}=\left\lceil1.25N_{raw}\right\rceil.
\]

The buffered count is then rounded upward to complete 250-event blocks. For a
single-layout net contrast this is
`ceil(N_buffered / 250)` blocks per endpoint configuration. For pooled
production, `N_pilot = 3,000` events per endpoint is the sum of three
1,000-event layout strata; its buffered count is divided equally across those
three strata before block rounding. The exact accepted 10% arithmetic is:

| Primary contrast | Observed `h` | Pilot events per endpoint | `N_raw` | `N_buffered` | Block calculation | Accepted events/configuration |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Pooled scintillation production | `0.175253` | `3,000` total (`1,000 x 3`) | `9,215` total | `11,519` total | `ceil(11,519 / (3 x 250)) = 16` | `16 x 250 = 4,000` per layout stratum |
| Observed net, `back-center` | `0.567095` | `1,000` | `32,160` | `40,200` | `ceil(40,200 / 250) = 161` | `40,250` |
| Observed net, `edge-center` | `0.282524` | `1,000` | `7,983` | `9,979` | `ceil(9,979 / 250) = 40` | `10,000` |
| Observed net, `back-four` | `0.307109` | `1,000` | `9,432` | `11,790` | `ceil(11,790 / 250) = 48` | `12,000` |

Here, `events/configuration` means one thickness-layout configuration, not the
sum of both endpoints. The pooled-production sizing floor is therefore
`3 x 4,000 = 12,000` events per endpoint, or `24,000` across its two endpoints.
The actual production endpoint samples are larger because the three layouts
receive their direct-net allocations; the equal-stratum estimator below uses
all of those available events without changing the `1/3` weights. The
corresponding maximum two-endpoint totals for the three direct-net contrasts
are `80,500`, `20,000`, and `24,000` events.

The 5% projections remain preserved in the analysis-v2 evidence but are not
selected for v1. They would require approximately four times the 10% samples
under the same `1/sqrt(N)` assumption and would still omit detector-model,
photoelectron, and electronics systematics.

## Six-thickness allocation

The accepted production allocation uses only the fixed `500 mm` absorber and
the six thicknesses `4, 8, 12, 16, 20, 24 mm`:

| Layout and thickness group | Configurations | Events/configuration | Blocks/configuration | Group events | Group blocks |
| --- | ---: | ---: | ---: | ---: | ---: |
| `back-center`, intermediate `8/12/16/20 mm` | `4` | `4,000` | `16` | `16,000` | `64` |
| `back-center`, primary endpoints `4/24 mm`, maximum | `2` | `40,250` | `161` | `80,500` | `322` |
| `edge-center`, all six thicknesses | `6` | `10,000` | `40` | `60,000` | `240` |
| `back-four`, all six thicknesses | `6` | `12,000` | `48` | `72,000` | `288` |
| **Maximum new production sample** | **18** | -- | -- | **228,500** | **914** |

All six `back-center` thicknesses receive the `4,000`-event pooled-production
minimum. Only its `4` and `24 mm` primary endpoints are eligible for staged
extension toward `40,250`; the four intermediate net points are secondary and
do not inherit the endpoint heavy-tail projection. `edge-center` and
`back-four` use their layout-specific endpoint allocation at all six
thicknesses to keep each full curve on a uniform within-layout sample design.
These choices do not promise 10% precision for every secondary intermediate
ratio.

The pooled-production estimand retains equal `1/3` layout-stratum weights even
when the net-response allocations make the production sample sizes unequal.
At production analysis time, bootstrap each layout at its available event
count, calculate its production mean, and average the three stratum means with
equal weights. Do not concatenate all production events into an
event-count-weighted mean, because that would make the staged `back-center`
extension silently redefine the pooled estimand. This equal-weight rule and
the v2 event-count-weighted pilot rule are numerically identical for the
sealed pilot because all three pilot strata contain 1,000 events.

The 30,000-event pilot is sizing evidence and is not counted in any production
total. Production uses new seed blocks and new immutable manifests. This keeps
the exploratory pilot separate from the sample on which the final production
intervals will be reported. Therefore `228,500` means the maximum number of
**new** events; it must not be reduced to `198,500` by subtracting or pooling
the pilot.

## `back-center` cumulative checkpoints

The two `back-center` primary endpoint configurations use the same new
production sample cumulatively across four predeclared checkpoints:

| Checkpoint | Cumulative events per endpoint configuration | Cumulative blocks/configuration | Added blocks/configuration | Added events across both endpoints | Projected `h` from pilot scaling |
| --- | ---: | ---: | ---: | ---: | ---: |
| `BC-S1` | `4,000` | `16` | `16` | `8,000` | `28.4%` |
| `BC-S2` | `10,000` | `40` | `24` | `12,000` | `17.9%` |
| `BC-S3` | `20,000` | `80` | `40` | `20,000` | `12.7%` |
| `BC-S4` | `40,250` | `161` | `81` | `40,500` | `8.9%` |

The projected column is diagnostic only:
`0.567095 x sqrt(1,000 / N)`. It shows why early stages test heavy-tail scaling
rather than being expected to meet the target immediately.

If all non-staged allocations and `BC-S1` are complete, the new sample is
`156,000` events in `624` blocks. Continuing the two endpoints to `BC-S2`,
`BC-S3`, and `BC-S4` adds, respectively, `12,000/48`, `20,000/80`, and
`40,500/162` events/blocks. The cumulative campaign ceilings are therefore:

| Last completed checkpoint | New production events | 250-event blocks |
| --- | ---: | ---: |
| `BC-S1` | `156,000` | `624` |
| `BC-S2` | `168,000` | `672` |
| `BC-S3` | `188,000` | `752` |
| `BC-S4` | `228,500` | `914` |

At every checkpoint, finalize and checksum the new stage, audit event and seed
identity, form the cumulative production sample, and rerun the primary
contrast plus tail/block diagnostics. Review at least:

- the bootstrap 95% relative half-width of `back-center 24/4`;
- maximum leave-one-250-event-block-out ratio shift;
- zero fraction and positive-event count;
- top-1% and top-5% response shares and the maximum event; and
- observed interval-width change relative to the prior checkpoint and the
  `1/sqrt(N)` projection.

The predeclared decision rules are:

1. **Precision success:** stop extending `back-center` if `h <= 10%` and the
   maximum leave-one-block-out shift is `<= 10%`.
2. **Continue to the next checkpoint:** the precision target is unmet, the
   block shift is `<= 20%`, and the interval width still decreases relative to
   the prior checkpoint. Progression always requires explicit review; no stage
   automatically submits the next one.
3. **Pause for method review:** the block shift exceeds `20%`, the interval
   fails to narrow, or a newly sampled extreme event materially worsens the
   top-tail diagnostics. Tail shares are review signals, not stand-alone
   pass/fail thresholds.
4. **Hard ceiling:** `BC-S4` is the maximum authorized sample. If the two
   success conditions are still unmet, do not add events automatically; reopen
   the statistical design.

For `BC-S1`, the sealed 1,000-event pilot interval is the comparison baseline
for the width-trend diagnostic only; its events are not merged into the
production estimate. Later stages compare against the immediately preceding
cumulative production checkpoint.

After the fixed allocations complete, the pooled-production, `edge-center`,
and `back-four` primary intervals must also be checked against `h <= 10%`.
Their sample sizes are not adaptive stages: if any one misses the target, do
not extrapolate and submit more events automatically; reopen the sizing
decision with its observed tail and block diagnostics.

Stopping is never based on whether the point ratio appears favorable, crosses
unity, or excludes unity. Because the cumulative percentile-bootstrap
interval is inspected repeatedly and is not adjusted as a formal sequential
confidence procedure, its nominal 95% coverage remains a pragmatic production
estimate rather than a strict confirmatory sequential guarantee. A request for
strict sequential coverage or an independent confirmatory sample would reopen
the statistical design.

## Claim boundary and next gate

The evidence supports the following statements:

- for the fixed `500 mm` slab proxy, thicker tiles create substantially more
  scintillation light;
- the production gain outweighs collection loss for `edge-center` and
  aggregate `back-four` over the `24/4` endpoint comparison;
- no direction of net change is resolved for `back-center`;
- `back-four` is an aggregate four-sensor result, not a normalized-efficiency
  ranking.

The evidence does not support:

- a claim that `500 mm` is a converged or official ePIC absorber;
- substitution of `200` or `300 mm` on equivalence grounds;
- absorber-size-independent thickness or layout conclusions;
- a universal production event count;
- a guarantee that every secondary ratio will reach 10% precision;
- a claim that checkpoint reuse constitutes a formal sequential 95%
  confidence procedure; or
- detector-model systematic, photoelectron, or electronics-response claims.

The statistical production design is now frozen. The next gate is engineering:
implement and validate immutable fresh-seed stages, exact cumulative task
selection, cross-stage checksum/audit provenance, and a non-overwriting
cumulative analyzer before submitting the first production stage.
