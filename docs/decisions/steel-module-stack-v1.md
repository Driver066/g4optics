# Steel Module Ten-Layer Stack v1

## Status and scope

This document freezes the implementation and execution contract for the
ten-layer longitudinal steel-module follow-up. It is independent of the
completed single-layer four-layout study and does not reinterpret or replace
that evidence.

The implementation uses the ordinary direct campaign route. It must not add a
managed preflight, held-submission control plane, or staged production
authorization mechanism.

## Geometry and source

The preset identity is `steel-module-stack-v1`. It has six configurations with
uniform tile thicknesses of `4, 8, 12, 16, 20, or 24 mm`. Every configuration
contains ten repeated `[40 mm SAE-304 steel][EJ-200 tile]` modules and uses the
same `edge-two` layout on every tile.

For tile thickness `t`, the complete stack length is

```text
L = 10 * (40 mm + t)
```

The stack is centered at `z=0`. Layers are numbered `0...9` from upstream to
downstream, and their centers are

```text
z_steel(i) = L/2 - i*(40 mm + t) - 20 mm
z_tile(i)  = L/2 - i*(40 mm + t) - 40 mm - t/2
```

There is no longitudinal gap at a steel-to-tile or tile-to-next-steel
interface. The painted/wrapped optical boundary is explicitly installed for
every ordered tile-to-neighbour boundary. The stack uses the accepted
polished-front-painted EJ-510 surface and undimpled zero-gap EJ-550 coupling
proxy; it does not add an explicit grease volume.

Each tile has two `2.4 x 2.4 x 0.5 mm` SiPM proxies on its `+X` face at local
centres `(-25, 0)` and `(+25, 0) mm`. Stable identity is

```text
global_sensor_copy = 2 * layer + local_sensor
```

and therefore spans `0...19`.

The source is a centered, point-like pencil neutron gun with `1 GeV` kinetic
energy and direction `(0, 0, -1)`. Its z coordinate is `L/2 + 1.5 mm`.

## Causal optical response

Every optical photon carries its originating tile layer. Scintillation and
Cerenkov photons acquire the layer at creation, while WLS and WLS2 descendants
inherit the original layer. A detected photon with unknown origin invalidates
accepted stack evidence.

For layer `i` and local sensor `j`, the primary collection metric is

```text
local_collection(i,j)
  = photons created in tile i and detected by sensor (i,j)
    / generated optical photons in tile i
```

Generated optical photons are tile-created scintillation plus Cerenkov
photons. Estimates use ratios of sums. A zero generated-light denominator is
reported with an explicit invalid flag, never replaced by numeric zero.

The event record also preserves all-origin sensor counts, the sparse
origin-to-destination transfer matrix, cross-layer leakage, full-stack
any-destination response, layer-resolved steel/tile energy deposition,
charged entries, and primary-neutron interaction counts.

## Statistical and execution policy

The controlling metrics are each thickness's full-stack same-layer causal
collection and same-layer causal net response. Per-layer and per-sensor
results receive intervals and low-statistics flags but do not independently
increase the production sample.

Use 10,000 percentile event-bootstrap resamples with NumPy PCG64 seed
`20260715`. The precision target is 10% relative half-width. Task-block
leave-one-out shift must be at most 20% to authorize production and at most 10%
to call the final result stable.

Execution is fixed to:

1. six one-event geometry-smoke tasks;
2. two 25-event endpoint benchmark tasks (`4` and `24 mm`);
3. four pilot blocks for each thickness (`24` tasks total);
4. at most one ordinary direct production array.

Let `r` be the slower benchmark wall seconds per event. Choose the largest
block size `B` in `{250, 100, 50, 25, 10}` satisfying

```text
1.5 * r * B <= 2700 seconds.
```

The benchmark and all later tasks request one CPU and a one-hour wall limit.
The pilot is cryptographically bound to the benchmark report; that report also
sets later memory to `max(2 GiB, ceil(1.5 * benchmark MaxRSS / GiB))`.

For a pilot estimate with relative half-width `h` and pilot size `N_pilot`,

```text
N_raw    = N_pilot * (h / 0.10)^2
N_target = ceil_to_block(max(N_pilot, 1.25 * N_raw), B)
```

Each thickness uses the larger target from its two controlling metrics. Pilot
events are included in final cumulative evidence; geometry-smoke and benchmark
events are not. Pilot plus production is capped at `120,000` total events, and
the production array is capped at `1,000` tasks. Invalid metrics, unknown
origin detections, a leave-one-out shift above 20%, or either workload cap
causes a review stop instead of automatic expansion.

No second production increment is automatic. The final analysis reports the
achieved precision and leaves any further simulation to a new human decision.

## Acceptance boundary

Implementation acceptance requires all six static geometry checks, human
visual review of the `4` and `24 mm` endpoints, preservation of existing
single-layer behavior, complete stack ROOT causal invariants, checksum-bound
campaign finalization, deterministic analysis, and non-empty headless PNG/PDF
figures.
