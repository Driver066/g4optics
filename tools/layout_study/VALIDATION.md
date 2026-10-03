# Reproducibility checks and current status

This is a draft research implementation. The fixed first 24,000-event scan is
specified in [FIRST_SCAN.md](FIRST_SCAN.md); scientific results are pending.

## Portable local check

The documented Docker route was exercised from a source archive with no `.git`,
with a newly installed public Geant4 11.4.2 runtime and a fresh data volume.
All twelve official datasets were downloaded and archive-checked; 43,526 unpacked
files were SHA-256 inventoried. Application compilation, tests and four ten-event
4 mm runs then succeeded with networking disabled, datasets read-only, and a
non-root user. The cold-check snapshot passed 68 tests (the final complete suite, including the runtime builder and scientific workflow,
then passed 77 tests in the same offline Docker environment).

All ROOT physics branches and histograms, aggregate CSV values and log bytes
matched the previously verified Mac sample for the same seeds. ROOT serialization
metadata were excluded. [The small reference summary](reference/mac-docker-t04.json)
records versions, conditions and counts. It is an engineering example, not a
layout ranking or a cross-architecture exact-output requirement.

The first cold check exposed an unreadable root-owned dataset checksum receipt.
The installer now makes its public checksum manifest readable by the non-root
runtime; the full offline check above passed after that fix. Physics files were
not changed.

## OSC background qualification

The C++ files packaged in this PR are byte-identical to the model previously
qualified on Geant4 11.4.2 x86_64 Serial at OSC: 24 configurations, 25 events each,
all process, geometry and event audits passed, and all 42 then-existing tests
passed with Python 3.9.21 / NumPy 2.0.2 / uproot 5.6.9. A downloaded independent
re-audit checked 611 archived files and all 600 events. The measured 25-event
runs took 56–311 seconds; the complete batch-step memory maximum was about
252 MiB. Those samples are excluded from the scientific scan.

The *exact final PR commit* must now be independently built and qualified on OSC
before its first scientific events. The scan manifest will record that commit,
not a moving main branch or an earlier executable. Environment, input, seed,
source and output checksums accompany the run. Progress, plots and interpretation
will be posted in the draft PR conversation.

## Limits

The inherited generated-light denominator and first-boundary diagnostic are
preserved. Log checks are not a complete optical birth/fate or all-trajectory
NoRINDEX audit. The SiPM response is collected optical photons, without PDE or
readout electronics. Functional checks and timing do not establish scientific
precision, experimental material agreement or a preferred detector layout.

[engineering-seeds.json](reference/engineering-seeds.json) lists seed values used
by the recorded smoke/qualification/benchmark runs, including the bundled local
example. Extend this list if you use additional engineering seeds before a new
independent scan. Do not mix these small samples into the first scientific scan.
