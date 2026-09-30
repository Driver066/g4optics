# Steel Module Stack v2: four layouts and readout-gap infrastructure

This contract implements the approved infrastructure scope. It does not select
a production gap, authorize an OSC submission, or interpret engineering smoke
events as a scientific sensitivity study. Stack-v1 evidence remains separate.

## Frozen physical choices

Use Geant4 11.4.2 in Serial mode, ten 500×500×40 mm SAE-304 slabs and ten
100×100 mm EJ-200 tiles. Each configuration has one common tile thickness in
4, 8, 12, 16, 20, 24 mm. SiPM proxies remain 2.4×2.4×0.5 mm, counting entry
through every face without PDE or readout electronics.

Retain every existing steel macro optical-process setting and scintillation
flag. In particular, retain optical-photon Scintillation; do not execute a
particle-specific inactivation. Cerenkov/Rayleigh/MieHG/OpWLS remain disabled
by the old macro. OpWLS2 retains its existing registration. Record actual
process activation in the runtime sidecar instead of inferring it from the
Geant4 version alone.

The source remains one centered 1 GeV kinetic-energy neutron per event,
point-like and directed along −Z.

## Geometry and public interface

The scan preset is `steel-module-stack-v2`; `--sipm-layout` and
`--readout-gap-mm` are mandatory. The latter is finite, >=0.5 mm, and bounded
by actual World/source containment. No production default is chosen.

Only the nine internal tile-to-next-steel interfaces receive gap g. Steel to
its downstream tile remains touching. With i=0..9 and dimensions in mm:

```
pitch = 40 + t + g
core_length = 10*(40+t) + 9*g
steel_z(i) = core_length/2 - i*pitch - 20
tile_z(i)  = core_length/2 - i*pitch - 40 - t/2
source_z   = core_length/2 + 1.5
```

The core center stays at z=0 for all layouts. The last tile has no downstream
steel slab and no tenth gap. A last-layer back SiPM projects 0.5 mm into World;
core length and actual detector extent are distinct metadata.

SiPMs touch their tiles. At g=.5 their backs touch the next steel slab; at g=1
there is .5 mm of air behind the chip. Unoccupied readout space is air, with
no invented PCB, package, extra optical layer, or steel cutout.

| layout | local sensor positions, mm | sensors/layer |
|---|---|---:|
| back-four | −Z: (−25,−25), (−25,+25), (+25,−25), (+25,+25) | 4 |
| back-two | −Z: (−25,−25), (+25,+25) | 2 |
| edge-two | +X: local (y,z)=(−25,0), (+25,0) | 2 |
| back-center | −Z: (0,0) | 1 |

The table order defines local IDs starting at zero. V2 uses global copy
`4*layer+local_sensor`; inactive slots are not physical sensors or zero-count
observations. V1 retains its old two-slot mapping.

Preserve painted tile→upstream-steel and tile→World surfaces. V2 must not add
a tile→downstream-steel surface across an air gap. Preserve the existing
tile→SiPM coupling behavior.

## Counting and analysis contract

Keep legacy scan, histogram, summary, G and D meanings. Legacy sensor columns
are projections of global copies 0..3, not the entire stack. V2 does not place
its variable-count sensor data into the old two-sensor stack rows.

An independent observer registers every optical birth, including optical
parents. Bind secondary addresses to final track IDs and promptly remove the
address binding. The ledger is event-local; suspension/resumption is not a
new birth or terminal outcome. Root origin and actual birth region are
separate, including explicitly identified outside-tile locations.

The four new trees are `stack_event_v2`, `stack_layer_v2`, `stack_sensor_v2`,
and `stack_photon_flow_v2`. Flows are sparse terminal-track aggregates, not a
full trajectory dump. Preserve old SiPM detection/kill behavior and random
draws. Optical and non-optical energy deposition are separately recorded.

Hard per-event accounting is total births = unique started = finalized = sum
of mutually exclusive fates. Primary, non-optical-parent, and optical-parent
birth partitions sum to total births. Sensor totals equal legacy D. Legacy G
is not required to equal all births. Unknown origin, duplicate/unbound birth,
unresolved track, unknown terminal, inactive sensor detection, and NoRINDEX
are errors; a known outside-tile origin is not an unknown origin.

Main response is all-origin collected photons per incident neutron. Report
legacy collection separately from birth-based collection, whose numerator
counts collection of the tile-born tracks themselves. Descendants do not
retroactively turn an absorbed parent into a collected track. Zero light is a
valid neutron event; zero denominators are invalid metrics, not zero ratios.

Use independent registered seed blocks, whole-event bootstrap within blocks,
10,000 resamples, a recorded fixed analysis seed, and original block event
weights. Different configurations are resampled independently. Report
differences/ratios with intervals without an automatic equivalence decision.

## Acceptance and preservation

The engineering allocation is 130 events: 48 one-event candidate geometries,
64 off/on events across the sixteen layout/thickness-endpoint/gap cases,
16 old-program/new-program compatibility events, and a two-event replay.
Test all 48 candidate configurations and parameter rejection before accepting
the feature. Exact old-output comparisons include ROOT events, histograms,
summary CSV and RNG-end state, excluding serialization metadata.

Preserve pre-change source, uncommitted content, accepted binaries and old
latest targets. Build reference and candidate independently. Run only frozen
inputs; bind task identities, seed/configuration registries, output checksums,
audits and receipts. Stop on a hard failure or fifteen-minute task timeout.
Never automatically expand an event allocation or a task timeout.

The initial science matrix has 8 configurations (two thickness endpoints,
back-center/edge-two, two gap candidates); the full candidate matrix has 48.
Generating a runnable scientific task list requires explicit event allocation,
blocks, seed and budget. This implementation only prepares those capabilities.
Acceptance status belongs to each batch's report, not to this contract.
