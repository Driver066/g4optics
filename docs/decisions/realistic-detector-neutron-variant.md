# Realistic Detector Neutron Variant — Decision Record

- **Status:** Confirmed scientific inputs; engineering design pending
- **Recorded:** 2026-07-13
- **Working branch:** `exp/realistic-detector`
- **Branch baseline:** `practice` at `50ec06d4`
- **Primary implementation area:** `test/OpNovice2`

## Purpose

This branch will add a simplified, more realistic detector variant to the existing EJ-200 tile and SiPM optical simulation:

1. place 4 cm of steel upstream of the scintillator tile;
2. replace the electron primary with a neutron;
3. compare tile thicknesses to determine whether increased neutron-induced light production outweighs the poorer optical collection observed for thicker tiles.

The first model is a local, single-absorber-plus-single-tile study. It is not yet a full multilayer sampling-calorimeter model.

## Authority and interpretation

The requirements below come from professor messages relayed on 2026-07-13. When a professor clarification differs from the current full-detector ePIC geometry, the explicit clarification governs this simplified study and the difference must be recorded.

The official geometry reference inspected for this record is the tagged ePIC release [`26.07.0`](https://github.com/eic/epic/tree/26.07.0). Future ePIC geometry changes are not automatically adopted.

## Confirmed decisions

### D-001 — Scientific question

**Decision:** Compare the competing thickness effects using quantities that keep light production and optical collection distinguishable.

The intended result should separate at least:

- scintillation photons produced per incident neutron;
- optical collection efficiency;
- photons entering the SiPM volume per incident neutron, which is the combined outcome.

In the current model, “SiPM detected photons” means optical photons entering the simplified silicon volume. It does not yet include a real SiPM photon-detection efficiency or electronics response.

### D-002 — Steel material and longitudinal thickness

**Decision:** Use the backward ePIC HCal absorber definition from release `26.07.0`:

- material: `StainlessSteelSAE304`;
- density: `7.9 g/cm3`;
- mass fractions: 74% Fe, 18% Cr, and 8% Ni;
- thickness along the beam direction: `4.0 cm`.

Sources:

- [`compact/hcal/backward_template.xml`](https://github.com/eic/epic/blob/26.07.0/compact/hcal/backward_template.xml#L10-L12)
- [`compact/materials.xml`](https://github.com/eic/epic/blob/26.07.0/compact/materials.xml#L423-L428)

The official absorber is a continuous 60-sided annular endcap layer, not one small steel plate per readout tile. Under the tagged geometry it spans approximately `r = 14–267 cm`. The `100 x 100 mm` readout segmentation must not be mistaken for absorber segmentation.

Sources:

- [`compact/definitions.xml`](https://github.com/eic/epic/blob/26.07.0/compact/definitions.xml#L665-L670)
- [`compact/definitions.xml`](https://github.com/eic/epic/blob/26.07.0/compact/definitions.xml#L693)
- [`compact/hcal/backward_template.xml`](https://github.com/eic/epic/blob/26.07.0/compact/hcal/backward_template.xml#L39-L61)

The transverse dimensions of the local steel proxy remain an engineering decision. They must be large enough that the result is insensitive to lateral edge leakage.

### D-003 — Steel-to-tile spacing

**Decision:** Use no geometric gap between the steel and the front face of the tile.

The current official ePIC `26.07.0` backward-HCal template contains a total `0.1 cm` layer gap, split into half-gaps around the active layer. The no-gap model is therefore a deliberate study-specific simplification based on the professor's direct clarification, not an exact copy of that full-detector geometry.

Source for the official gap: [`compact/hcal/backward_template.xml`](https://github.com/eic/epic/blob/26.07.0/compact/hcal/backward_template.xml#L10-L18)

Implementation consequence: the tile front face will border steel rather than the world volume. Its optical boundary must be defined for `tile -> steel`; the other tile faces still border their actual neighboring volumes. The exact surface implementation is deferred to engineering design.

### D-004 — Neutron momentum and Geant4 energy input

**Decision:** Treat the professor's original neutron momentum as the source of truth:

```text
p_neutron = 1 GeV/c
```

Energy is a derived quantity, not a second free parameter. Using

```text
m_neutron c^2 = 0.939565 GeV
E_total = sqrt((pc)^2 + (m c^2)^2)
T_kinetic = E_total - m c^2
```

gives:

```text
E_total     = 1.372145 GeV
T_kinetic   = 0.432580 GeV
```

Geant4 GPS `/gps/energy` expects kinetic energy. The corresponding fixed-primary commands are therefore:

```text
/gps/particle neutron
/gps/energy 432.58 MeV
```

The professor's later phrase “if energy then about 2 GeV” is retained as an order-of-magnitude explanation, not interpreted as a separate instruction to use 2 GeV kinetic energy.

Run metadata must preserve both the requested momentum and the derived Geant4 input, for example:

```text
requested_momentum: 1 GeV/c
gps_kinetic_energy: 432.58 MeV
total_energy: 1.372145 GeV
energy_derivation: relativistic momentum-to-kinetic-energy conversion
```

### D-005 — Primary model scope

**Decision:** The first implementation will model one upstream steel absorber and one scintillator tile. The purpose is to isolate the competition between neutron-induced light production and tile light collection.

The following are not implied by this first scope:

- a full repeated steel/scintillator stack;
- complete ePIC backward-HCal geometry;
- calorimeter-level energy reconstruction;
- a realistic SiPM PDE or electronics chain.

Those can be considered only after the single-layer model is understood.

## Current repository facts relevant to implementation

- `OpNovice2` currently uses `FTFP_BERT` together with `G4OpticalPhysics`; the existing physics list already provides neutron elastic, inelastic, and capture processes in the energy range needed here.
- The current detector geometry has no steel absorber volume.
- The primary source is backed by `G4GeneralParticleSource`, but the scan runner and metadata are still electron/Sr-90 oriented.
- The current beam convention is from `+Z` toward `-Z`; adding 4 cm of steel requires absorber-aware source placement.
- Existing output already separates generated/scintillation photon counts, collection efficiency, and photons entering the SiPM volume.

These statements describe the starting point. They are not approval of a particular implementation.

## Open engineering decisions

The following must be decided after this record is accepted and before implementation is considered complete.

### E-001 — Local absorber transverse proxy

Choose a configurable local slab size and an edge-leakage convergence check. The full `14–267 cm` annular ePIC layer should not be copied blindly into the current single-tile world.

### E-002 — Geometry and configuration interface

Decide the absorber fields, messenger commands, defaults, validation rules, source-position calculation, and `run_config.json` representation.

### E-003 — Optical boundary at direct contact

Define how the existing tile surface model applies to the new `tile -> steel` front boundary, including whether it represents an implicit coating or a bare material interface.

### E-004 — Controlled comparison baseline

Select the tile dimensions/thicknesses, optical surface preset, SiPM geometry and placement, dimple state, and coupling configuration that remain fixed while thickness is varied.

### E-005 — Beam geometry and scan shape

Decide whether the first study uses centered point incidence, a finite beam profile, or a position scan. A centered smoke/comparison run and a later spatial scan are separate stages.

### E-006 — First-pass observables and shower diagnostics

Separate the minimum model-building output from later validation quantities such as steel/tile energy deposition, inelastic-interaction flags, charged secondaries entering the tile, zero-light event fractions, and event-level fluctuations.

### E-007 — Validation and production statistics

Define geometry visualization, one-event and low-statistics smoke tests, runtime benchmarking, convergence checks, and only then production event counts. Existing electron-study event counts must not be copied automatically to a GeV-scale neutron shower with optical tracking.

### E-008 — Local and OSC workflow

Decide how the new source and absorber parameters enter the local runner, point-plan generator, Slurm workflow, merge tools, and reproducibility metadata.

## Invariants for the next phase

- Preserve `1 GeV/c` as the authoritative neutron input and derive the GPS kinetic energy.
- Preserve 4 cm `StainlessSteelSAE304` as the absorber material/thickness reference.
- Preserve the professor-confirmed no-gap geometry and document its difference from ePIC `26.07.0`.
- Keep the steel transverse proxy configurable and verify that its edges do not control the result.
- Change one scientific variable at a time when comparing tile thicknesses.
- Record enough metadata to reconstruct the geometry, source, physics environment, and derivation later.

## Source statements retained verbatim

Initial professor description:

> So the idea is to test in geant the more realistic detector. Which means 2 things. Adding 4cm of steel in front of the tile (between tile and particle gun) and then shooting neutrons instead of electrons.
>
> The reason. Our current studies show that thicker tiles collect less light but our old studies show that thicker tiles create more light from neutrons. So we now should see which effect is stronger.
>
> The neutron should have momentum of about 1 gev
>
> The calorimeter is made to measure neutral particles energy. But neutral particles don't create light in scintillation crystals, only charged particles do
>
> So you need to convert an uncharged particle like a neutron into a bunch of charged particles. You can do that by putting a very dense material in its way. It won't interact electromagnetically but if it hits a nucleus in the material it will interact and "shower" or break out a bunch of new particles. Some will be electrons and protons which will create light in the acintilators.
>
> So by combining layers of dense material with scintillating crystal you create a "sampling calorimeter" the dense material makes the neutral particle shower charged particles and the scintillator measures the amount of these. Together with many layers you can measure the total energy of the original neutral particle

Follow-up clarifications:

> We can find in the official epic geometry files.
>
> No gap
>
> If energy then about 2 GeV (1 GeV of mass and 1 GeV of momentum)
