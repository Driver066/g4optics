# Issue #9 infrastructure acceptance — 2026-10-27

The seven-case controlled 24 mm geometry and engineering infrastructure passed
local acceptance. This does not establish a light-yield ranking, focusing
mechanism, statistical precision or full-tile uniformity.

The [agreed upstream plan](https://github.com/jdbrice/g4optics/issues/9#issuecomment-6441837362)
and [model/execution guide](README.md) define the scope. All identities and
evidence hashes are in [validation.json](validation.json).

## Implemented

- A–G: flat/hemisphere with 2 mm and 6 mm external SiPMs, compensated
  hemisphere, and compensated/uncompensated equal-volume cubic well.
- Matching front material is part of the same Boolean scintillator solid.
  The cube has a small inward joining overlap, preserving its exact exposed
  height. No new optical boundary, material or physics process is introduced.
- Default-off geometry probes and the existing passive optical observer;
  all original SiPM counting rules and legacy production fields are retained.
- Frozen source/template/tool/input snapshots, a bound build receipt, explicit
  geometry and smoke stages, per-case audits, and refusal to overwrite or
  silently retry an attempted case.

## Executed acceptance

| Check | Result |
|---|---|
| Geant4 11.4.2 Release build, linux/arm64, Serial | passed |
| Independent full dataset-file checksum verification | passed |
| New geometry: 7 valid and 7 invalid configurations | 14/14 passed, zero events |
| Actual solid probes for each valid case | 9 rays and 45 Inside probes passed |
| Old geometry modes and rejection cases | 12/12 passed, zero events |
| Seven-case optical smoke check | 21 tasks, 42 events accepted |
| Historical 5 mm reference/off/on comparison | 6 tasks, 30 events accepted |
| ROOT/CSV/final-RNG comparisons | all 4 passed |
| New tooling tests | 10 passed |
| Existing optical-audit tests | 8 passed |

Exactly 72 optical engineering events were executed. The 42-event sample uses
three positions and two events per geometry/position. It is too small for the
scientific comparisons; the counts are acceptance evidence only. Every event,
including any zero response, is retained by the audit policy.

An independent read-only review checked the frozen/current source and tool
hashes, all per-case receipts and all 42 event rows. Its 357 count/source
invariants passed. Two events had G > 0 and D = 0 and were retained. Independent
old/new comparisons also reproduced all four ROOT/CSV/RNG equality results.

For flat and hemisphere at the historical 5 mm thickness, the accepted old
executable and new default-off executable produced identical contents for all
7 legacy ROOT trees / 114 branches, all 4 active histograms including flow bins
and errors, byte-identical summary CSVs, and byte-identical final RNG states.
The same checks passed between new diagnostics off and on. ROOT serialization
metadata are excluded. These are finite matched-seed regression checks, not
proof of equivalence for every possible input.

Geometry probes directly interrogate the constructed Geant4 solid; the volume
fields are analytical, not independent numerical volume measurements. The
optical checks verify the real z=18 mm source, per-event production/birth/start
accounting, unique detection, e± deposition, photon-birth centroid validity,
ROOT/CSV consistency and absence of unexpected warnings. They do not close the
entire optical terminal-fate or boundary-history ledger.

## Preserved local evidence

- `outputs/issue9-environment/`: read-only data-volume verification, dataset
  inventory and immutable local image metadata. The complete checksum command
  succeeded before the simulation; the runtime also checks inventory identity.
- `outputs/dimple-mechanism/engineering-v2/`: accepted source/build, 14 geometry
  logs and receipts, and 21 ROOT/CSV/RNG smoke outputs.
- `outputs/dimple-mechanism/legacy-geometry-v1/`: the 12 historical zero-event checks.
- `outputs/dimple-mechanism/legacy-regression-v1/`: frozen old/new executables,
  six matched-seed outputs and the four comparisons.
- `outputs/dimple-mechanism/engineering-v1/`: earlier source build and passed
  geometry checks. No optical events were run there. It was superseded after
  fixing a completion-marker whitespace check in the Python audit; its files
  were retained, and the final batch was prepared separately.

These output directories are local ignored evidence, not uploaded data. The
historical `tools/dimple_study/results/line-v1/` and its source/hash checks remain
unchanged. The current implementation is on `codex/issue9-dimple-mechanism`.

## Next scientific step

Define a pilot with sufficient independent events to estimate variance and
choose the formal comparison precision and position grid. The two-event smoke
sample must not determine a geometry ranking. A focusing claim additionally
needs an explicitly controlled optical source or position/angle diagnostic.
No formal campaign or OSC job was started during this acceptance.
