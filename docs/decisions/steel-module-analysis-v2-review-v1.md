# Steel Module Analysis-v2 Review and Absorber Policy

Status: analysis-v2 evidence reviewed; fixed-reference absorber policy accepted;
production precision and event counts remain open

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

This decision closes the v1 absorber-policy gate. It does not authorize a
production campaign: the precision target, staged treatment of the
heavy-tailed `back-center` response, and final event counts remain open.

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

## Precision projections remain unaccepted

Analysis-v2 produces separate 5% and 10% review projections for the four
primary contrasts:

| Primary contrast | 10% events/configuration | 5% events/configuration |
| --- | ---: | ---: |
| Pooled production, per layout stratum | `4,000` | `15,500` |
| Observed net, `back-center` | `40,250` | `161,000` |
| Observed net, `edge-center` | `10,000` | `40,000` |
| Observed net, `back-four` | `12,000` | `47,250` |

These values assume the observed 95% relative half-width scales as
`1/sqrt(N)`, apply a `1.25` safety factor, and round upward to complete
250-event blocks. They are internally reconciled review aids, not accepted
production sizes. In particular, `back-center` should be staged and rechecked
rather than committed in one step because its finite-sample behavior differs
most from a smooth normal approximation.

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
- automatic acceptance of a 5% or 10% precision target; or
- detector-model systematic, photoelectron, or electronics-response claims.

The next decision is the precision and staged-event policy. No production
campaign is authorized until that policy and its exact per-layout event counts
are accepted and recorded.
