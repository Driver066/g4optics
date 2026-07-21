# Steel Module Direct BC-S1 Execution and Analysis Record

Status: direct BC-S1 execution completed and finalized; preliminary v1 analysis
completed; checksum-bound LOO/tail adapter implemented for OSC review

Study preset: `steel-module-scan-v1`

Record date: 2026-07-20

## 1. Purpose of this record

This document is the human-readable audit trail for the first production-sized
back-center endpoint increment. It records the scientific setup, the pilot
reasoning, the change from the abandoned managed/preflight route to the ordinary
campaign route, the actual Slurm execution, finalization, preliminary statistics,
and the remaining review gate.

The machine-readable companion is produced by:

```bash
python3 hpc/osc/analyze_steel_module_direct_bc_s1.py \
  --campaign-dir "$DIRECT" \
  --pilot-campaign-dir "$CAMPAIGN"
```

It writes an immutable `finalized/direct-analysis` directory containing input
identities, endpoint estimates, 32 leave-one-block-out records, heavy-tail
diagnostics, pilot-to-production precision trajectory, a human-readable summary,
and `SHA256SUMS`. It neither submits Slurm work nor runs Geant4.

## 2. Fixed scientific model

The professor's updated request and the accepted
[`steel-module-scan-v1`](steel-module-scan-v1.md) decisions define this model:

| Component | Fixed value |
| --- | --- |
| Primary particle | neutron |
| Authoritative beam quantity | `1 GeV` kinetic energy |
| Beam | centered point gun, normal incidence, pencil direction |
| Tile | `100 x 100 mm`; endpoint thicknesses `4` and `24 mm` in BC-S1 |
| Steel | SAE 304 proxy, `500 x 500 x 40 mm` |
| Steel-to-tile spacing | no air gap |
| Tile boundary | painted/wrapped optical treatment retained at the steel-facing boundary |
| Optical baseline | polished-front-painted EJ-510 plus undimpled zero-gap EJ-550 coupling proxy |
| Readout layout | one SiPM at the center of the downstream `-Z` face (`back-center`) |
| Physics/reference scope | results apply to the fixed `500 mm` transverse steel-slab proxy |

The `500 mm` transverse size is a fixed v1 model reference. The pilot did not
prove it equivalent to an infinite absorber or to the complete official ePIC
calorimeter geometry.

## 3. Evidence path before production

The accepted sequence before BC-S1 was:

1. Geometry and source decisions were frozen in
   [`steel-module-scan-v1.md`](steel-module-scan-v1.md).
2. The three SiPM layouts and representative thicknesses passed manual geometry
   visualization.
3. The 18-task geometry smoke passed finalization and event audit.
4. The four-task benchmark established that a 250-event block fit comfortably
   inside the one-hour task target.
5. The 120-task convergence pilot sampled 30 configurations with four independent
   250-event blocks each: 30,000 audited events total.
6. Analysis-v2 identified `back-center 24/4` as the limiting heavy-tailed primary
   contrast: ratio `0.742`, 95% interval `[0.465, 1.307]`, relative half-width
   `56.71%`, and maximum pilot block-omission shift `19.22%`.
7. The accepted staged policy therefore assigned BC-S1 exactly 16 new blocks at
   each endpoint: 4,000 events at `4 mm`, 4,000 events at `24 mm`, 32 tasks and
   8,000 events total.

The sealed pilot events are a historical width/tail baseline only. They are not
pooled into the new production estimator.

## 4. Submission-route correction

An earlier Phase-2B/Phase-2C design introduced held preflights, readiness locks,
successor executions, intent journals, and a separate control plane. It produced
useful incident history but was disproportionate to this ordinary 32-task Geant4
array. Several pre-simulation jobs failed in that route without consuming a
production event or production seed.

On 2026-07-20 the current route was deliberately simplified:

- preserve the already frozen BC-S1 task/seed mapping and physics identities;
- convert it into a standard steel-module campaign;
- use the same ordinary submission/finalization path that had already completed
  the 120-task pilot;
- retain campaign, attempt, ROOT, event-audit, and checksum evidence;
- remove held preflight, readiness, intent, and successor requirements from the
  current BC-S1 path.

Historical managed/preflight files remain in the repository only for incident
provenance. They are not the current runbook and must not be interpreted as a
prerequisite for BC-S1 analysis or later ordinary campaign execution.

## 5. Actual BC-S1 execution

| Identity | Recorded value |
| --- | --- |
| Direct-route implementation commit | `ddea5462c3a01f39a83af3b9cfa964053cbde626` |
| Campaign | `sm-v1-production-bc-s1-direct-c89b659643dd` |
| OSC campaign directory | `$WORK/campaigns/steel-module-production-bc-s1-direct` |
| Slurm array job | `50615141` |
| Shape | `4/24 mm`, `back-center`, `500 mm` absorber, blocks `0-15` |
| Tasks | `32` |
| Events | `8,000` (`250` per task; `4,000` per endpoint) |
| Completion | `32/32 COMPLETED`, exit `0:0` |
| 4 mm task elapsed range | approximately `1:12-2:09` |
| 24 mm task elapsed range | approximately `5:39-10:49` |
| Finalization | passed |
| Integrated event audit | passed |
| Invalid attempt results ignored | `0` |

The successful execution demonstrated that no special compute-node preflight was
needed for this production increment. The ordinary workflow reached finalized
scientific data directly.

## 6. Exact command route

The campaign was generated and checked from the clean OSC checkout:

```bash
MANAGED="$WORK/campaigns/steel-module-production-bc-s1"
DIRECT="$WORK/campaigns/steel-module-production-bc-s1-direct"
FROZEN="$WORK/frozen-campaign-sources"

python3 hpc/osc/generate_steel_module_direct_bc_s1_campaign.py \
  --managed-child-dir "$MANAGED" \
  --out-dir "$DIRECT"

python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --project-root "$REPO" \
  --g4-data-root "$DATA_ROOT" \
  --check-only
```

The accepted check-only output reported exactly `32` tasks and `8,000` events.
The ordinary Slurm array was then submitted:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --project-root "$REPO" \
  --account PAS2524 \
  --g4-data-root "$DATA_ROOT" \
  --frozen-root "$FROZEN"
```

After all 32 tasks completed, the standard finalizer produced the sealed event
evidence:

```bash
python3 hpc/osc/finalize_steel_module_campaign.py \
  --campaign-dir "$DIRECT"
```

The preliminary generic analysis was run as:

```bash
python3 hpc/osc/analyze_steel_module_campaign.py \
  --campaign-dir "$DIRECT" \
  --production-block-events 250
```

The dedicated review adapter is the next and final analysis command for BC-S1:

```bash
python3 hpc/osc/analyze_steel_module_direct_bc_s1.py \
  --campaign-dir "$DIRECT" \
  --pilot-campaign-dir "$CAMPAIGN"
```

## 7. Preliminary finalized result

The generic 10,000-resample event bootstrap produced:

| 24/4 quantity | Point ratio | Bootstrap 95% interval |
| --- | ---: | ---: |
| Scintillation production | `6.117831` | `[5.270064, 7.116985]` |
| Optical collection | `0.268996` | `[0.221707, 0.329606]` |
| Observed net SiPM response | `1.645670` | `[1.286898, 2.106082]` |

The factorization is physically interpretable:

```text
production gain x collection ratio = observed net ratio
6.117831       x 0.268996         = 1.645670
```

Within this fixed model, increasing the tile from `4` to `24 mm` produces about
`6.12` times as much scintillation light while retaining about `26.9%` of the
relative collection efficiency. The production increase is stronger: the mean
observed SiPM response is about `64.6%` higher, and this preliminary interval
excludes unity.

The net interval's relative half-width is `24.89%`, so BC-S1 has not reached the
accepted `10%` precision target. Effect direction and precision are distinct:
the current sample resolves a positive net ratio while still being too broad to
finish the staged precision program automatically.

## 8. Dedicated LOO/tail evidence contract

`analyze_steel_module_direct_bc_s1.py` reads only existing checksum-valid inputs:

- the direct campaign and its frozen 32-task plan;
- finalized validation, configuration totals, event audit, and 32 audited ROOT
  files;
- the sealed pilot's checksum-valid `analysis-v2` primary identity and the eight
  back-center endpoint pilot ROOT blocks from which the already-tested
  production-analysis core recomputes the baseline distribution diagnostics.

It reuses the reviewed production-analysis core and emits:

- `endpoint_estimates.csv`;
- `distribution_diagnostics.csv`;
- `pilot_distribution_diagnostics.csv`;
- `block_loo.csv` with exactly 32 omissions;
- `precision_trajectory.csv`;
- `primary_contrast.json`;
- `numeric_eligibility.json`;
- `pathway_decomposition.json`;
- `analysis_config.json` and `summary.md`;
- `SHA256SUMS` covering every output.

The analysis records its Git commit/tree, analyzer/core hashes, Python/NumPy/
uproot versions, campaign/finalization/pilot identities, Slurm attempt/job IDs,
the finalized task-index and event-audit hashes, and one digest over the 32
audited ROOT hashes. It refuses to overwrite an existing output directory.

This is proportional evidence hardening, not a return to the abandoned control
plane: there is no preflight, checkpoint, readiness lock, intent, scheduler
contact, or new simulation.

## 9. Remaining progression decision

No next child is submitted automatically. Human review uses three independent
questions:

1. Is the production interval narrower than the sealed pilot interval?
2. Is the maximum leave-one-250-event-block-out ratio shift no greater than the
   accepted `20%` continuation ceiling?
3. Did the new 4 mm or 24 mm SiPM distribution show a material worsening in
   zero rate or top-1%/top-5% tail concentration relative to the pilot?

If the interval remains above `10%`, narrows versus the pilot, maximum LOO is at
most `20%`, and human tail review finds no material worsening, BC-S2 is eligible
for a separate explicit decision. BC-S2 would add 24 new 250-event blocks per
endpoint: 48 tasks and 12,000 new events, bringing each endpoint to 10,000 new
production events cumulatively.

Otherwise the correct outcome is `pause-review`. The direct analyzer reports
numeric eligibility only; it does not record a human decision or submit BC-S2.

## 10. Claim boundary

The present evidence supports this scoped statement:

> For a centered 1 GeV kinetic-energy neutron pencil beam, the fixed
> 500 x 500 x 40 mm SAE-304 slab proxy, the retained painted/wrapped boundary,
> the EJ-550 coupling proxy, and a back-center SiPM, the 24 mm tile has a larger
> mean detected optical response than the 4 mm tile in the 8,000-event BC-S1
> sample because its neutron-induced production gain exceeds its collection
> loss.

It does not yet support a 10%-precision final production claim, absorber-size
independence, equivalence to the complete ePIC calorimeter, or automatic
progression beyond BC-S1.
