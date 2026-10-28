# Independent pilot preparation — 2026-10-28

The mechanism pilot has been prepared and verified. **No scientific task,
Geant4 process, container, dataset preflight or OSC job was started in this
preparation.** The new run/audit paths were tested offline with fixtures.

The [scientific protocol](PILOT.md) fixes seven configurations, 25 two-dimensional
positions, two independent 100-event blocks, and a total budget of 35,000
incident electrons. Its aim is mechanism curves and variance estimation;
full-tile uniformity is outside this pilot.

## Frozen package

- Directory: `outputs/dimple-mechanism/pilot-v1/`.
- Input manifest SHA256:
  `71ccbc55fdb010b8971de95dc0edae29444afca8d66ab50f3404a26a2280651b`.
- State: **350 unstarted tasks; zero attempted, failed or accepted tasks;
  zero pilot events**.
- 717 frozen files include all 350 macros, the accepted binary/source/evidence,
  the runtime tools, the statistical protocol and the historical seed records.
- 700 unique pilot seed values have no overlap with the 218 known historical
  seeds extracted from six hash-verified sources.
- The accepted binary remains
  `65b5700fd5c54a07162bcf3838b01efe30eada1d4d906869cb839e1da8cea27d`,
  using the pinned linux/arm64 Geant4 11.4.2 image recorded in the manifest.

The copied engineering ROOT files under the accepted-evidence portion of the
package are historical checks, not newly generated pilot events. Each of the
350 scientific task directories contains only its `run.mac`.

## Verification

All 20 new offline tests passed: nine model/output-audit tests and eleven
runner/input-integrity tests. They cover real 100-row ROOT/CSV/RNG fixtures,
nonzero and negative y, source and event-ID errors, zero response and zero
denominators, optical-parent births, modified inputs, atomic single attempts,
failure preservation, explicit manifest identity and incomplete finalization.
The old geometry, physics and engineering tools were not changed.

The frozen runner's `verify` and `status` passed. An independent read-only
review checked all 717 file hashes, all 350 macros, the full seed exclusion
chain, the accepted source/binary and statistical protocol, and absence of
pilot attempts or outputs. Details are in [pilot_preparation.json](pilot_preparation.json).

The preparation figure is stored at
`outputs/dimple-mechanism/pilot-plan-v2/pilot-positions.png` with an SVG companion.
It displays source positions and analytic aperture/sensor footprints only;
it is not a simulated response map. Recreate it with `plot_pilot_plan.py`.

## Execution boundary

Future execution must explicitly select this input-manifest identity, complete
the read-only full dataset preflight, and claim each registered task once.
The current binary is the accepted local ARM64 baseline. A different platform
requires a separately recorded same-source build and qualification.

The future analysis will keep the pilot distinct from engineering and later
confirmatory samples. Whole-event uncertainty, zero-response handling and the
limited interpretation of sparse mechanism curves are fixed in the protocol.
No effect-size, focusing or achieved-precision conclusion is made here.
