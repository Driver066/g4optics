# Dimple study preparation

Source baseline: `404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5`.
Branch: `codex/dimple-study`.

## Agreed first geometry (2026-10-07)

Start with group 3 of `DimpleCheck.md`: a single 100 × 100 × 5 mm tile,
a central hemispherical dimple of radius/depth 3 mm, and a 6 × 6 × 0.5 mm
SiPM proxy **outside** the bottom face. Its top face is at z = −2.5 mm and
its centre is at z = −2.75 mm. The flat control has the same SiPM dimensions
and position. The square covers the complete circular opening in projection.
Both use bare polished surfaces, air in the cavity, and no optical grease.
This is a diagnostic geometry, not a claim about a painted physical sample.

The new placement name is `external`. Existing `opening` puts the small SiPM
inside the cavity, and `surface` moves it towards the curved wall. These older
modes retain their footprint restrictions. External placement with grease is
not part of the agreed model.

## First acceptance boundary

Prepare and run zero-event geometry checks before producing optical samples:

- flat 6 mm and external-dimple 6 mm: identical sensor position and dimensions;
- flat 2 mm, opening 2 mm and surface 2 mm: existing placement regressions;
- oversized sensors in the old modes and external-plus-grease: rejected;
- no sampled volume overlaps, and normal completion for valid cases.

These checks establish geometry preparation only. They do not validate optical
transport, collection efficiency, or the three requested scientific scans.
No steel result, existing executable, global launcher, or historical output is
updated by this workflow. All new builds and evidence belong under this
worktree's `outputs/dimple-study/`.

The dedicated `geometry_check.py` entrypoint uses the new mode directly. The
historical `run_sipm_cavity_scan.sh` still accepts only `opening`/`surface`;
do not use that wrapper to launch an external-mode campaign yet.

## Reproduce the geometry checks

Use a new batch directory for every attempt. Preparation snapshots the current
application source, records its hashes and Git state, and writes 12 macros.

```sh
python3 -B tools/dimple_study/geometry_check.py prepare \
  --batch outputs/dimple-study/geometry-new
```

In the existing Geant4 11.4.2 environment, bind this worktree to `/work` and
the verified data to `/g4data` read-only. Build the **snapshot**, then run:

```sh
cmake -S outputs/dimple-study/geometry-new/source/test/OpNovice2 \
  -B outputs/dimple-study/geometry-new/build \
  -DCMAKE_BUILD_TYPE=Release -DWITH_GEANT4_UIVIS=OFF
cmake --build outputs/dimple-study/geometry-new/build --parallel 2
G4RUN_MANAGER_TYPE=Serial python3 -B tools/dimple_study/geometry_check.py run \
  --batch outputs/dimple-study/geometry-new \
  --executable outputs/dimple-study/geometry-new/build/OpNovice2
```

These commands run in the mounted environment, not against a shared historical
container's checkout. Image/data identities and build logs must accompany each
new batch. The checker writes per-case logs and receipts plus `verification.json`.
It refuses to replace existing attempts and never calls `beamOn`.

## Completed geometry preparation

The final `geometry-v2` batch passed **12/12 cases**, with **zero simulated
particle events**. Six valid cases completed normally; six invalid cases
stopped with the expected fatal configuration error. The valid checks verify
the actual G4Box sensor dimensions, one sensor, its world position, and the
completed Tank/SiPM sampled overlap checks. Flat-large and external-large both
have a 6 × 6 × 0.5 mm sensor centred at (0, 0, −2.75) mm.

The build used Geant4 11.4.2, Serial, linux/arm64 and the existing immutable
image `sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee`.
The existing dataset volume was verified against its complete checksum receipt
before the first build and mounted read-only throughout. No environment or
data installation was performed.

[The saved validation summary](geometry-validation.json) binds source hashes,
the executable, inputs, logs, image and dataset receipt. Local complete source
snapshots, build logs, macros and receipts are in
`outputs/dimple-study/geometry-v2/`. The initial 11-case `geometry-v1` remains
preserved; v2 adds explicit actual-size/completion checks and a World-boundary
negative case. Current acceptance is v2.

The [small optical/counting check](OPTICAL_CHECK.md) has now passed: 30 executed
regression events and 60 smoke events, with explicit material tables, source,
seeds and observer noninterference checks. The geometry-only macros still must
not be used for an efficiency scan by merely adding `beamOn`.

## Physics and counting

Retain the baseline Geant4 11.4.2 physics registration and optical process
settings. Do not add an opticalphoton Scintillation disable command. The old
W08 diagnostic code is not imported. Its rejected photon-closure result must
not be described as a validated baseline.

The optical engineering check distinguishes produced-photon and collected-photon
definitions. The existing G counter is a legacy production
counter, not a complete count of every optical birth; D counts optical entry
into the SiPM proxy and does not include PDE or electronics. A geometry PASS
does not establish G equals all optical tracks or prove full photon closure.
The new passive observer confirmed that optical-parent births account for the
difference between legacy G and all optical starts in this small sample.

The user has accepted the rendered geometry and authorized the
[5,200-event local line scan](LINE_SCAN.md). Its execution state and outputs
remain separate from the completed engineering checks.

Groups 1 and 2 remain later work. For group 1, the intended compensating
hemisphere is above the tile, preserving material thickness along z.
