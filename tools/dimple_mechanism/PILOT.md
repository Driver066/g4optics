# Independent mechanism pilot — 2026-10-28

The owner approved preparation of an independent scientific pilot focused on
mechanism curves. This preparation does not execute the pilot or submit a
cluster job. The simulation model and accepted C++ source are those published
at `38c94b29969438626774a8d4424d2f662de167b9`; see [the model](README.md) and
[engineering acceptance](VALIDATION.md).

## Fixed scientific sample

All seven A–G configurations use the same 25 source positions, with two
independent blocks of 100 incident electrons per configuration/position:

**25 positions × 7 configurations × 2 blocks × 100 events = 35,000 electrons,
350 tasks, and 175 configuration/position cells.**

The source remains one 1 MeV electron at z = 18 mm, directed along −Z, with
bare polished surfaces, an air cavity and no grease. Keep the existing material
tables, finite 100 mm World-air absorption, physics registration, SiPM entry
definition and legacy/all-birth counters. No physical parameter is retuned.

| Position family | Coordinates in mm | Count |
|---|---|---:|
| Center | (0, 0) | 1 |
| Axis | (s, 0), s = 0.8, 1.2, 1.7, 2.1, 2.8, 3.2, 10, 40, 48 | 9 |
| Diagonal | (s, s), s = 0.8, 1.2, 1.7, 2.1, 2.3, 3.2, 10, 40, 48 | 9 |
| Symmetry checks | (−10, 0), (0, 10), (0, −10), (−10, −10), (−10, 10), (10, −10) | 6 |

The central points straddle the 2 mm SiPM half-width (1 mm), cubic-well
half-width (approximately 1.9192 mm), hemisphere opening (radius 3 mm), and
6 mm SiPM half-width (3 mm). On the diagonal, the circular rim is at
s = 3/sqrt(2), approximately 2.1213 mm. Source positions do not lie exactly
on these geometric edges.

Axis and diagonal are different paths: at the same parameter s their radial
distances are s and sqrt(2)*s. Preserve both x and y in manifests, outputs and
analysis. The point (48,48) is near a corner, whereas (48,0) is near one edge.
These diagnostic positions are not an area quadrature, a two-dimensional
uniformity map, a dense resolved mechanism curve, or evidence of spatial
convergence. Sparse curve segments are visual guides only.

## Independence and provenance

- The deterministic pilot seed range is 2026102801–2026103500: 700 distinct
  positive integer values, one pair per task. These designate separate streams;
  numerical uniqueness is not a mathematical proof of stream independence.
- [The exclusion registry](pilot_seed_exclusions.json) records 218 known
  historical/engineering seed values from six bound manifests. Geometry-only
  seeds were read from macros after verifying their manifest hashes. This is
  the scope of the collision check, not a registry of unrelated simulations.
- The 42 issue #9 engineering events, 30 regression events, prior 5 mm results,
  and any rejected attempts are excluded from this pilot's scientific sample.
- A later confirmatory scan will have a separately frozen design and new
  seeds. Pilot events will not be merged into that sample.
- The accepted executable is reused unchanged; source, executable, template,
  runtime tools, macros, seed exclusions and acceptance evidence are frozen
  together. The preserved accepted snapshot includes an older trailing blank
  line in an unused regression helper; the published whitespace-only change
  is recorded in `validation.json`, without relaxing C++/binary identity.

The current accepted binary is linux/arm64 and is tied to its recorded local
Geant4 11.4.2 image. The position/task design is portable; executing on a
different architecture requires a separately recorded build and qualification
from the same source. This preparation is not an OSC deployment or acceptance.

## Planned comparisons

Let mu_A through mu_G denote mean collected photons per incident electron at
the same fixed (x,y). Report point estimates and exploratory intervals for:

| Question | Comparison |
|---|---|
| Hemisphere effect with small/large SiPM | mu_B/mu_A − 1; mu_D/mu_C − 1 |
| Does enlargement depend on the cavity? | (mu_D/mu_B)/(mu_C/mu_A) − 1 |
| Front restoration within each recess shape | mu_E/mu_B − 1; mu_F/mu_G − 1 |
| Compensated shape designs | mu_F/mu_E − 1 |
| Supporting area and uncompensated-shape controls | mu_C/mu_A − 1; mu_D/mu_B − 1; mu_G/mu_B − 1 |

The shape comparisons concern complete specified designs. Equal removed volume
does not equate their depth, opening or surface area. Front restoration also
changes the first material intersection and the photon-birth distribution.
Neither a collection-ratio change nor the area interaction alone establishes
focusing or a separate causal optical-loss fraction. A later fixed optical
source experiment would need its own primary/polarization/counting audit.

## Analysis contract

Analysis begins only after all 350 tasks and 35,000 event records pass audit.
The observation is an incident electron, identified by (task,event_id), not a
photon or one of two block means. Retain every event, including D=0 and G=0;
invalid ratios remain explicitly undefined instead of filtering events.

For each of the 175 cells, report:

- N=200, both block means, mean D, unbiased event variance of D, standard error
  of the mean and the zero-response fraction;
- mean legacy G, mean all optical births, ratio of totals sum(D)/sum(G), and
  entry-per-birth ratio sum(D)/sum(all births), with denominator validity;
- e± energy deposition separately from total tile deposition, and valid
  event-level photon-birth centroid summaries with their valid-event counts.

Use whole-event resampling to retain covariance among D, G, births and energy.
For the planned exploratory intervals, use 20,000 bootstrap replicates,
resampling 100 events independently within each of the two blocks and then
combining their equal-size samples. Use independent streams across cells and
configurations, with recorded analysis seed 20261028 and stable cell ordering.
Identical event IDs across configurations do not create paired observations.
Save the input hashes and replicate arrays so endpoints can be checked.

Use descriptive pointwise 95% percentile intervals. No whole-curve, whole-map,
or family-wise significance claim follows from those intervals. Undefined
point ratios remain null; if any bootstrap replicate has an invalid denominator,
report its count and mark that ratio interval unavailable rather than dropping
replicates. Inspect individual blocks, symmetry checks and distribution tails
without removing unfavorable events or treating a two-block check as proof
of stability.

An all-zero response cell or zero observed variance is precision-unresolved:
do not interpret a degenerate bootstrap interval as proof of an exact zero
response, or use it to justify zero additional formal events. Flag these
cells explicitly when proposing the formal design.

Plot the axis and diagonal with their actual numeric spacing, providing a
central view and a full-distance view. Symmetry checks remain separately
identified. Do not pool the nonuniform positions into a tile-average response
or uniformity statistic.

## Precision planning and completion

The pilot has a fixed event budget; there is no effect-dependent stopping,
automatic sample extension or hidden replacement of failed tasks. It estimates
response distributions and event-level variances, explores the mechanism
curves, and supports a proposed formal sample-size allocation.

A candidate formal target remains a 95% interval half-width of at most
**5 percentage points for a relative mean-D change**, e.g. a change estimated
at −20% with an interval of roughly −25% to −15%. This is neither a 5% relative
error on the change itself nor an already achieved precision. The formal
contrast family, simultaneous coverage policy, spatial grid and tolerance
still need to be fixed before a confirmatory campaign. Treat pilot-derived
sample sizes as planning estimates, including uncertainty in the variance.

Successful preparation means all 350 immutable inputs are generated and
verified, the 700 seed values pass the recorded exclusions, the accepted
source/build evidence matches, and no scientific task has been executed.
Execution and statistical analysis have separate completion records.

## Preparation and later execution

From the worktree, prepare a new directory using the accepted local build and
the checked seed registry. Preparation never launches Geant4 or a container:

```sh
python3 -B tools/dimple_mechanism/pilot.py prepare \
  --batch outputs/dimple-mechanism/pilot-v1 \
  --accepted outputs/dimple-mechanism/engineering-v2 \
  --seed-exclusions tools/dimple_mechanism/pilot_seed_exclusions.json
```

The printed digest identifies `input-manifest.json`. Subsequent checks use the
frozen runtime copy. These commands remain read-only with respect to task state:

```sh
python3 -B outputs/dimple-mechanism/pilot-v1/source/tools/dimple_mechanism/pilot.py \
  verify --batch outputs/dimple-mechanism/pilot-v1
python3 -B outputs/dimple-mechanism/pilot-v1/source/tools/dimple_mechanism/pilot.py \
  status --batch outputs/dimple-mechanism/pilot-v1
```

Before later execution, use the pinned image and a read-only `/g4data` mount,
with `G4RUN_MANAGER_TYPE=Serial`, `ISSUE9_IMAGE_ID` equal to the frozen image ID,
and `ISSUE9_DATASET_MANIFEST=/g4data/layout-study-SHA256SUMS`. The `preflight`
action checks every dataset file and records the actual runtime. `run-task`
accepts one exact registered task ID; `finalize` requires all 350 audited tasks.
These three actions require `--manifest-sha256` with the prepared digest.
They are separate from preparation and are not invoked during this work.

Each task receives an atomic claim and at most one attempt. Successful receipts
are published only after output audit; failed or interrupted attempts remain
preserved. Distinct tasks may be scheduled concurrently by an explicit executor;
the pilot tool imposes no worker cap and submits no jobs. Finalization rejects
missing, failed, changed or extra outputs. A failure requires diagnosis and an
explicit recovery decision, not an automatic replacement sample.

The prepared directory contains all original macros, the copied accepted
executable/source/evidence, seed-history records, runtime tools, and this
protocol. Old build/reproduction identities are retained without editing their
historical manifests. The independent diagram can be regenerated with:

```sh
python3 -B tools/dimple_mechanism/plot_pilot_plan.py --output NEW_FIGURE_DIRECTORY
```
