# Issue #9: controlled dimple mechanism infrastructure

This implements the [agreed plan](https://github.com/jdbrice/g4optics/issues/9#issuecomment-6441837362).
It is a new 24 mm model, based on `codex/dimple-study` at `68153c0b`.
The historical 5 mm source, result bundle and reproduction checks remain frozen.
The scope here is geometry and engineering validation, not a scientific campaign.

The independently prepared 35,000-electron mechanism pilot is specified in
[PILOT.md](PILOT.md). Its separate runner freezes 350 tasks, reuses the accepted
binary, and preserves the engineering sample and checks unchanged. See
[the preparation record](PILOT_PREPARATION.md) for the verified input identity
and current zero-execution state.

## Fixed model

- A single 100 × 100 × 24 mm EJ-200 tile, nominal front/back at z = ±12 mm.
- Bare polished interfaces, air recess, no paint, optical grease, steel or stack.
- The inherited material tables and optical process registration are retained,
  including 100 mm World-air absorption length and optical-parent Scintillation.
- One back-center, external SiPM proxy, 0.5 mm thick. Small is 2 × 2 mm;
  large is 6 × 6 mm. Both receiving planes are z = −12 mm and centers z = −12.25 mm.
- Hemisphere radius/depth 3 mm. The square well is an equal-volume cube with
  side `(2*pi*3^3/3)^(1/3)` mm (approximately 3.8383 mm), not a matched-depth well.
- Front compensation adds the corresponding aligned hemisphere or cube to the
  same scintillator solid. It restores the removed volume and the vertical
  material thickness at each transverse position; it adds no internal material interface.
- All source electrons start at z = 18 mm, above the tallest front feature,
  with 1 MeV energy and direction −Z. The added front material changes the
  first intersection and may change deposition and the photon-birth distribution.

| Case | Recess | Front compensation | SiPM |
|---|---|---|---|
| A | none | none | small |
| B | hemisphere | none | small |
| C | none | none | large |
| D | hemisphere | none | large |
| E | hemisphere | matching hemisphere | small |
| F | equal-volume cube | matching cube | small |
| G | equal-volume cube | none | small |

A/B/C/D supply the complete shape-by-area comparison. B/E and G/F compare
compensation within each recess shape. E/F compare two specified compensated
designs; equal volume does not make their depths, openings or surface areas equal.
An area interaction or a change in D/G alone does not establish optical focusing.

## Geometry controls

The existing `/opnovice2/dimple/enabled`, `/radius` and `/sipmMode external`
commands are retained. New opt-in controls are:

```text
/opnovice2/dimple/mode hemisphere|cube
/opnovice2/dimple/frontCompensation true|false
/opnovice2/diagnostics/issue9Geometry true|false
```

Front compensation defaults off. Cube and compensated modes require the
external, center-back, single-sensor, no-grease geometry. The radius remains the
reference hemisphere radius in cube mode. Geometry probes inspect the actual
Geant4 solid with `Inside` and vertical ray traversal, rather than treating
printed input parameters as proof of the constructed shape.

## Measurements and acceptance

The engineering sample uses x = 0, 3.2, 10 mm, y = 0, with two events per
configuration/position: 21 tasks and 42 events. These are bounded smoke checks,
not a resolved line scan, ranking, uncertainty estimate or full-tile average.
Zero responses remain valid; events are not selected or replaced by response.

The existing D counter counts entry through any face of the ideal SiPM proxy,
without PDE/electronics. Legacy G counts non-optical-parent production; the
passive observer separately records all optical births and unique started tracks.
Its accounting is not a terminal-fate or full optical-path ledger. Preserve
`electron_tile_edep_mev` (e± deposition) separately from total tile deposition,
which includes optical deposition. Photon-birth centroid fields remain available.

Acceptance requires valid geometry and source records, expected active volumes,
no unexpected warnings or overlaps, per-event birth/start and detection checks,
ROOT/summary agreement and immutable source/input/build identities. Separately,
matched-seed historical 5 mm flat/hemisphere runs compare all legacy ROOT
contents, summary bytes and final RNG state against the accepted old executable.

## Execution boundary

The tooling prepares a new source/input/runtime snapshot, builds that snapshot
and runs explicit geometry or smoke phases. It rejects reused attempts and
changed frozen inputs. Use a fresh directory after a failed attempt; retain the
failed evidence. There is no automatic retry, adaptive event extension, OSC
submission or production-scan command.

The formal position grid, statistical precision and any targeted optical
position/angle experiment are future study decisions. The retained World-air
absorption is a controlled model assumption, not a measurement of real air.

## Prepare and run

On the host, from this worktree, first validate the existing Geant4 dataset
against its complete checksum inventory. Supply the immutable image ID and a
host copy of that verified inventory. For example:

```sh
python3 -B tools/dimple_mechanism/engineering.py prepare \
  --batch outputs/dimple-mechanism/engineering-new \
  --image-id sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee \
  --dataset-manifest outputs/issue9-environment/layout-study-SHA256SUMS
```

Use that image with this worktree mounted at `/work`, the verified data mounted
read-only at `/g4data`, no network, and these environment variables:

```sh
G4RUN_MANAGER_TYPE=Serial
ISSUE9_IMAGE_ID=sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee
ISSUE9_DATASET_MANIFEST=/g4data/layout-study-SHA256SUMS
```

Run the frozen tool copy inside the container, in sequence:

```sh
cd /work
python3 -B outputs/dimple-mechanism/engineering-new/source/tools/dimple_mechanism/engineering.py \
  build --batch outputs/dimple-mechanism/engineering-new
python3 -B outputs/dimple-mechanism/engineering-new/source/tools/dimple_mechanism/engineering.py \
  run --batch outputs/dimple-mechanism/engineering-new --stage geometry
python3 -B outputs/dimple-mechanism/engineering-new/source/tools/dimple_mechanism/engineering.py \
  run --batch outputs/dimple-mechanism/engineering-new --stage smoke
```

The runtime checks inventory identity. It does not substitute for the preceding
full dataset-file checksum check. Preserve its independent receipt alongside
the build and geometry/smoke verification records. Geometry volume fields are
analytic quantities; the ray/Inside fields are finite probes of the actual solid.

## Historical regression

`regression.py prepare --output NEW --engineering ENGINEERING_BATCH --reference OLD_BINARY`
requires the exact accepted old dimple optical executable and a successful new
build receipt. It freezes both binaries, the original optical template and its
own runtime. Run `NEW/runtime/tools/dimple_mechanism/regression.py run --output NEW`
inside the same Geant4 environment. The fixed sample is 30 events: flat/dimple
at 5 mm thickness, each with old binary, new features off, and new diagnostics on.
Both old/new and off/on comparisons include the final RNG state. The historical
science sample and its seeds are not reused or appended to.
