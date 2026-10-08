# Small optical and counting check

The local engineering batch `outputs/dimple-study/optical-v1/` passed on
2026-10-08. It executed **90 electron events**: 30 regression events followed
by 60 independently seeded smoke events. This is preparation for the dimple
study, not a spatial scan, precision result, or geometry ranking.

## Frozen inputs

- Reference C++ source: `52fc522fe1391e6ae0171d3e3f5c9a69b465b7cb` and its
  already accepted `geometry-v2` executable. That binary was copied unchanged.
- Candidate: the same geometry and physics with default-off passive event
  counters and independently enabled random-state output. The exact source,
  build commands/log, binary, checker and input hashes are retained.
- Both geometries: one 100 × 100 × 5 mm tile; 6 × 6 × 0.5 mm external SiPM,
  centre (0, 0, −2.75) mm. Dimple radius/depth 3 mm. Bare polished, air cavity,
  no grease, steel, stack or bottom-cavity preset.
- One fixed-energy 1 MeV electron per event, from (x, 0, 4) mm along −Z.
  The six smoke cases are flat/dimple × x = 0, 3.2, 10 mm, ten events each.
- Material and optical commands come from `ej200_sipm_gps_test.mac` at the
  reference commit. EJ-200 nominal yield is 10,000 photons/MeV, RINDEX 1.58,
  absorption length 3,800 mm, rise time 0.9 ns and decay time 2.1 ns. The
  emission spectrum is copied verbatim. World RINDEX is 1.0003 and its
  absorption length is **100 mm**, the inherited finite-absorption air model.
  SiPM remains the existing G4_Si entry proxy (RINDEX 4, absorption length
  1 µm). Birks constant remains 0.126 mm/MeV.
- Existing optical process activation is preserved. In particular, no
  opticalphoton Scintillation disable command is added. WLS2's existing
  process setting is not changed; the actual other-process birth count must
  be zero in this engineering sample.
- Fixed seed pairs are recorded before execution and reset after the geometry
  overlap sampling. Regression comparisons deliberately reuse a seed pair;
  the six smoke cases have distinct pairs, disjoint from regression seeds.

Do not call `/opnovice2/gun/randomDirection false`: the inherited messenger
ignores its argument and enables random direction. These independent processes
retain the false default and set `/gps/direction 0 0 -1`. The new diagnostic
rows verify the actual primary count, PDG, direction and energy.

## Validation performed

For each geometry, five events were run with the reference binary, five with
the candidate observer off and five with it on. Both reference/off and off/on
comparisons passed for all **7 legacy ROOT trees / 114 branches**, all **4
active histograms** (including flow bins and errors), and byte-identical summary
CSV files. ROOT serialization metadata are excluded.

The off/on comparison additionally requires byte-identical `result.rng`, saved
after ROOT output closes via `G4Random::saveFullState`. The old reference has
no end-state output, so **reference/off does not claim an RNG-state comparison**.
The installed Geant4 does not expose `/random/showEngineStatus`; zero-event
command probes identified that before any optical event was run. The built-in
`saveThisRun`/`saveThisEvent` commands preserve start states rather than a new
end-state snapshot; see the [versioned Geant4 implementation](https://github.com/Geant4/geant4/blob/v11.4.2/source/run/src/G4RunManager.cc).

All 60 smoke events passed source, geometry, nonnegative integer counters,
event-ID uniqueness, ROOT/summary consistency and the birth/start checks below.
All 12 processes exited normally, with no retries or replacement samples.
Six independent ROOT-fixture tests also passed, including rejection of corrupt
counts, source/geometry, unknown warnings, altered histograms and RNG states.

## Counting definition

`dimple_optical_event_v1` is added only when
`/opnovice2/diagnostics/dimpleOptical true` is set before initialization. It
counts optical secondaries from the current step before the existing SiPM early
return, classifies their parent as optical/non-optical and their creator as
Scintillation/Cerenkov/other, and independently counts unique started tracks.
Repeated PreTracking callbacks after suspension are recorded as resumes.
SiPM detections are independently checked for duplicates and missing starts.

In this sample, event by event:

```text
legacy G = non-optical-parent Scintillation births
all optical births = legacy G + optical-parent Scintillation births
unique optical starts = all optical births  (no optical primaries)
legacy D = detection calls = unique detected tracks <= unique optical starts
```

Across the 60 smoke events, **G = 541,439**, optical-parent births = **2,730**,
and all births = unique starts = **544,169**. These are pooled accounting totals
across deliberately different positions and geometries, not a tile-average
efficiency estimate. Legacy D totals 84,337 and retains the original all-face
SiPM entry definition; no PDE or electronics response is added.

The old G/D columns remain unchanged. `collection_efficiency` still means D/G
with its legacy denominator. This observer does **not** provide terminal-fate
closure, a full optical path audit, or proof of physical material accuracy.
The inherited NoRINDEX log check retains its first-boundary diagnostic scope.

## Reproduction and evidence

Use a new batch directory. First prepare on the host:

```sh
python3 -B tools/dimple_study/optical_smoke.py prepare \
  --batch outputs/dimple-study/optical-new \
  --reference outputs/dimple-study/geometry-v2/build/OpNovice2
```

Use the existing immutable image
`sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee`,
mount this worktree to `/work` and the verified Geant4 data to `/g4data` read-only,
with no network and `G4RUN_MANAGER_TYPE=Serial`. Inside that environment:

```sh
cd /work
python3 -B tools/dimple_study/optical_smoke.py build \
  --batch outputs/dimple-study/optical-new
python3 -B tools/dimple_study/optical_smoke.py run \
  --batch outputs/dimple-study/optical-new \
  --candidate outputs/dimple-study/optical-new/build/OpNovice2
```

The runner binds the candidate to its frozen-source build receipt, checks
executable hashes before and after every task, and stops on any failed audit.
It preserves existing attempts and never automatically retries or extends
the sample. The reference binary must match the earlier geometry validation;
reproducing this batch requires that retained local reference artifact.

The tracked [validation summary](optical-validation.json) binds all identities,
comparison outcomes and six smoke reports. Complete source/tool snapshots,
ROOT files, CSVs, macros, RNG states, logs and receipts remain in the local batch.
The data volume passed full file-checksum verification before this build.
The candidate changes no physics registration, material definition, geometry
placement or legacy counting rule.
