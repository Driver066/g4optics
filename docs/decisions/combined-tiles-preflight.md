# Issue #7: combined tiles — baseline preflight

Date: 2026-10-10 (America/New_York).

Historical preflight record: provisional decisions and execution status below describe this initial check only. See the [current archived study checkpoint](combined-layouts-dual-readout.md#阶段归档与后续接续2026-10-16) for the completed implementation, results, published diagnostics, and work not yet started.

Issue: https://github.com/jdbrice/g4optics/issues/7

This preflight establishes that the existing monolithic reference model can run
the requested beta spectrum with Side 2. It does not implement or validate the
8+8 / 8+16 mm joined geometry, estimate an equivalence margin, or rank thicknesses.

## Confirmed by the owner

- Start a separate branch/worktree from `main@404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5`.
- Use the existing beta spectrum (`sr90_allowed_beta_v1`, Sr-90/Y-90 GPS spectrum).
- Use Side 2.
- The laboratory optical grease is EJ-550. The existing model's nominal index
  1.46 is the candidate for the future inter-tile interface. Manufacturer:
  https://eljentechnology.com/products/accessories/ej-550-ej-552
- Docker startup and small local checks are authorized; lateral dimensions and
  other laboratory conditions remain to be discussed.

## Created work location

- Branch: `codex/combined-tiles-study`.
- Worktree: `/Users/edison/.codex/worktrees/combined-tiles-study/g4optics`.
- All 34 source/template/spectrum files listed in the manifest match the exact
  starting commit. No C++ or existing model/tool files were changed.
- The old dirty checkout and prior study containers, builds and outputs were
  preserved. No commit, push, issue update or formal campaign was performed.

## Provisional engineering configuration

| Parameter | This check | Status |
|---|---|---|
| Scintillator | Single EJ-200, 100 x 100 x 16 or 24 mm | Lateral size provisional |
| Source spectrum | Unchanged `spectra/sr90_allowed_beta_v1_gps.mac` | Confirmed spectrum |
| Source position | (0,0,9.5) / (0,0,13.5) mm | 1.5 mm above entrance face; engineering assumption |
| Source direction/profile | -Z, central pencil beam | Beam profile not calibrated here |
| Side 2 | Same +X face, y = -25/+25 mm, z = 0 | Existing layout interpretation |
| SiPM size | 2.4 x 2.4 x 0.5 mm | Existing Side 2 reference; provisional |
| SiPM centers | (50.25,-25,0), (50.25,25,0) mm | Same for both thicknesses |
| External surface | Bare polished, no wrapping | Engineering assumption, not laboratory sign-off |
| SiPM coupling | Existing zero-gap entry proxy; no PDE/electronics | Inherited model |
| Steel / stack / dimple | Disabled | Single-tile reference |
| Inter-tile grease | Absent; joined geometry not yet implemented | Future work |

The existing `/opnovice2/grease/enabled` is a separate, bottom SiPM coupling
feature incompatible with Side 2. It was disabled; this does not reject the
confirmed EJ-550 inter-tile interface.

The shell scan preset restricts `--sipm-layout` to neutron studies. This preflight
therefore used the existing macro generator directly, with explicit `edge-two`,
and inserted the unchanged beta spectrum. No neutron preset or fixed-energy
override was used. Geometry-only macros contain no `/run/beamOn` command.

The 50 x 50 mm option cannot retain this exact Side 2 layout: centers at y=+/-25
mm plus a 1.2 mm half-width exceed the face. Smaller tiles would need a separate
decision about sensor positions.

## Execution and evidence

Evidence root: `outputs/combined-tiles/20261010-baseline-v1/` in this worktree.
This ignored directory is retained locally, not published or backed up by Git.

- Fresh Release build, Geant4 11.4.2, linux/arm64, Serial.
- Offline, non-root container with 2 CPU / 4 GiB limit, read-only source and data
  mounts, independent writable build/output directory.
- Image ID: `sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee`.
- Executable SHA256: `2ea15a2a3df8063d659e6fcf269fc2b0174093b5931192496d26c81f03807b7b`.
- Manifest SHA256: `ad42facd965377c046eb007de5fb1fbcb751b9cefbcda9448efb5a77b853e54e`.
- Seeds: 16 mm `(20261010,7016)`; 24 mm `(20261010,7024)`. Seeds are reset
  after geometry sampling. Each case retains its run/event RNG state files.
- Existing dataset installation receipt and its hash were retained. This check
  did not rehash every extracted dataset file or install/download datasets.

| Check | 16 mm | 24 mm |
|---|---:|---:|
| Zero-event geometry process exit | 0 | 0 |
| Geometry overlap sampling | Passed, 10,000 samples per tested volume | Passed, same setting |
| Beta events | 10 | 10 |
| Beta process exit | 0 | 0 |
| Primary entries at expected +Z face | 10 | 10 |
| Sensor 0 + sensor 1 = total, every event | Passed | Passed |
| ROOT counts / summary CSV consistency | Passed | Passed |

All 20 events were retained. Primary energies varied within the source support;
the source coordinates and entrance-face hits were checked. Generated/scintillation
counts, inactive sensors, energy-deposition components, event IDs and efficiency
definitions were checked. The only reported Geant4 exception was the inherited
`Analysis_W001` warning that ntuple merging does not apply to Serial execution.
An independent read of the source hashes, macros, ROOT files and summaries
confirmed these checks. The legacy console summary still prints the GPS nominal
energy as 1 MeV; the actual sampled energies are recorded per event in ROOT and
are non-monoenergetic. Do not use that old console field to identify this source.
After validation, the dedicated container was stopped; Docker remains available.

Files: `manifest.json`, `source-audit.json`, `geometry-audit.json`,
`smoke-audit.json`, `image.json`, `container.json`, build logs, and for each case
`geometry/` and `smoke/` with exact macro, full log and process receipt.
`smoke/` additionally contains ROOT, CSV summary and RNG states. `prepare.py`,
`run.py`, and `audit.py` preserve the preparation/execution/audit procedures.
Preparation is intentionally tied to a fresh output directory and refuses to
overwrite existing case directories; running these scripts blindly is not a
request for more events.

## Interpretation and next discussion

This is a 20-event engineering check of the monolithic references. It is not a
statistical spectrum validation, a laboratory reproduction, a full optical
birth/terminal closure audit, or a joined-tile equivalence result. The inherited
first-boundary diagnostics do not prove complete absence of NoRINDEX throughout
every photon history. Geometry sampling is not an exhaustive geometric proof.

Before joined geometry is implemented, resolve the intended lateral dimension,
outer wrapping/paint, source geometry, sensor size and the 8+16 orientation.
Use an explicit unpainted EJ-200 / EJ-550 / EJ-200 interface as the candidate
assembly, with external wrapping treated separately. The proposed 0.1 mm joint
makes the physical assembly 16.1 / 24.1 mm thick if the plastic pieces remain
8+8 / 8+16 mm; record both active thickness and envelope thickness.

The formal comparison should distinguish total collected light, production and
collection efficiency, retain zero-response events, and define a quantitative
equivalence tolerance before selecting its event budget. No budget or scientific
acceptance threshold was chosen in this preflight.
