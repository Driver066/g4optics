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

Implementation consequence: the tile front face will border steel rather than the world volume. Its optical boundary must be defined for `tile -> steel`; the other tile faces still border their actual neighboring volumes. D-009 records the confirmed reflector treatment at this contact.

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

### D-006 — Local steel absorber proxy and convergence policy

**Decision:** Represent the local portion of the continuous ePIC absorber with a configurable rectangular steel slab. Keep its transverse dimensions fixed across all tile configurations so that absorber containment does not become an additional tile-dependent variable.

Use `500 x 500 x 40 mm` as the production-size candidate, where `40 mm` is the confirmed beam-axis thickness. Before production, run centered-beam transverse-size checks with:

```text
200 x 200 x 40 mm
300 x 300 x 40 mm
500 x 500 x 40 mm
```

Adopt the `500 x 500 mm` transverse size after the `300 x 300 mm` and `500 x 500 mm` results agree within statistical uncertainty for both scintillation photons per incident neutron and photons entering the SiPM per incident neutron.

All later position scans must also validate that every beam position, together with the required shower margin, remains inside the absorber footprint.

### D-007 — Controlled tile and optical baseline

**Decision:** The first neutron comparison will use only the two established `50 x 50 mm` EJ-200 tile configurations and vary thickness from `4 mm` to `16 mm`.

Keep the following optical configuration fixed across both thicknesses:

- surface preset: `polishedfrontpainted`;
- reflectivity model: repository empirical EJ-510 curve;
- SiPM: `2.4 x 2.4 x 0.5 mm`, centered on the `-Z` face;
- dimple: disabled;
- SiPM coupling: current undimpled zero-gap proxy (`optical_coupling=none`);
- steel absorber: the same D-006 slab configuration for both tiles;
- scintillator and optical-process properties: unchanged from the current practice baseline.

`polishedfrontpainted` is the best current working surface proxy under the primary 50 mm-tile shape metric. It is not claimed to uniquely identify the physical finish. The measured approximately `115 x 115 mm` tile groups and the prospective 24 mm-thick tile are excluded from the first neutron comparison.

### D-008 — Scope of the 55 mrad divergence choice

**Decision:** Use `55 mrad` as the nominal divergence for the first neutron study because D-007 intentionally includes only the two `50 x 50 mm` tile groups.

This choice is scoped and provisional:

- in the 5000-event, 50 mm-only refinement, `55 mrad` is the numerical minimum of the primary all-point scaled-RMSE metric;
- the same refinement supports a broad `45–75 mrad` plateau rather than a uniquely resolved divergence;
- when the two measured approximately `115 x 115 mm` groups are included, the four-sample calibration does not select 55 mrad: shape and inside-tile metrics favor `45 mrad`, while all-point chi-square favors `75 mrad`;
- therefore `55 mrad` must not be described as the global best value for all four tile groups.

If either `115 x 115 mm` group is added to this neutron study, the divergence decision must be reopened with `45 mrad` and `75 mrad` as the established candidates. The present use of 55 mrad records a deliberate dataset-scope choice, not a universal beam calibration.

### D-009 — Reflector retained at the no-air-gap steel contact

**Decision:** The tile remains painted or wrapped at the face next to the steel. The professor's “no air-gap” clarification means that the steel contacts the outside of the retained reflector; it does not mean that the reflector is removed to create a bare EJ-200-to-steel optical interface.

For the D-007 first comparison, retain the current EJ-510 painted baseline and represent the coating as an implicit optical boundary rather than an explicit volume:

- add an ordered `tile -> steel` border surface on the steel-facing tile face;
- give that boundary the same `polishedfrontpainted`, UNIFIED `dielectric_dielectric`, and empirical EJ-510 reflectivity model used by the controlled optical baseline;
- retain the existing painted `tile -> world` treatment on tile faces that still border the world;
- do not use a bare `dielectric_metal` steel interface in the primary comparison.

The simplified geometry may therefore place the tile and steel solids directly adjacent while the zero-thickness optical surface stands in for the retained coating. This preserves zero **air** gap without falsely removing the paint/wrap. A bare-steel boundary would be a separate sensitivity model and would require its own steel optical properties.

The professor's wording also covers wrapped tiles in general. It does not imply that every future wrapping system should use EJ-510 properties; EJ-510 is selected here specifically because D-007 fixes the current painted 50 mm-tile baseline.

### D-010 — Staged center, one-dimensional, and two-dimensional study

**Decision:** Build up the spatial study in three stages rather than starting with a full two-dimensional production scan:

1. run the controlled `4 mm` versus `16 mm` comparison at the tile center, `x = 0`, `y = 0`;
2. after the centered configuration passes validation, run a one-dimensional line scan through the tile center, using identical positions and source settings for both thicknesses;
3. add a two-dimensional scan only after the center and one-dimensional results are understood.

In the first two stages, “point” refers to the transverse source-position distribution. It does not mean a zero-angle beam: retain the D-008 nominal `55 mrad` angular divergence. Do not automatically copy the earlier electron-study `sigma = 5 mm` transverse profile into this neutron study.

Because the D-007 geometry is nominally symmetric in `x` and `y`, an `x` scan at `y = 0` is the default line-scan interpretation. D-011 fixes the first line-scan grid and symmetry check; a finite transverse profile remains a later-stage decision.

### D-011 — First one-dimensional scan grid

**Decision:** After the centered comparison is validated, use the following point-position line scan for both tile thicknesses:

```text
y = 0 mm
x = 0, 5, 10, 15, 20, 25, 30, 35, 40 mm
```

Reuse the validated centered result at `x = 0` rather than rerunning an identical configuration. The `x = 25 mm` point lies on the nominal edge of the `50 x 50 mm` tile; the `30–40 mm` points probe shower spill-in when the nominal beam position is outside the tile.

Also run `x = -20 mm`, `y = 0` as a symmetry and geometry-offset cross-check. Its result must agree with the corresponding `x = +20 mm` result within statistical uncertainty before the positive-half scan is interpreted as representative of the full line.

All positions retain the D-008 `55 mrad` angular divergence and the D-010 point-like transverse source-position distribution. A finite beam spot and the later two-dimensional grid are post-first-pass decisions, not blockers for implementing the center and one-dimensional stages.

### D-012 — Event-level causal observable model

**Decision:** Preserve a compact per-event record of the complete causal chain

```text
incident neutron
-> interaction in steel
-> charged-particle transport into the tile
-> energy deposited in the tile
-> optical photons produced
-> photons entering the SiPM
```

The production ntuple and mergeable run summary must contain the following quantities.

#### Steel interaction record

- total energy deposited in steel, `steel_edep_mev`;
- primary-neutron `elastic`, `inelastic`, and `capture` interaction counts in steel;
- derived boolean flags for each interaction category and an `any_interaction` flag.

These are diagnostic records, not switches that enable or disable physics. They are not mutually exclusive: for example, one primary neutron may scatter elastically before undergoing an inelastic interaction. `elastic` records direction/energy-changing nuclear scattering, `inelastic` identifies nuclear excitation or breakup that produces the main shower-like final state, and `capture` records neutron absorption after any slowing. Capture may be rare for the initial fast neutron but remains useful as an audit category.

Classify interactions from the Geant4 process category or subtype for the primary-neutron track while it is in steel, rather than relying only on fragile process-name string matching.

#### Transport into the tile

- number of unique charged tracks entering the tile for the first time;
- summed kinetic energy at first entry;
- entry counts and summed kinetic energy split into `electron/positron`, `proton`, and `other charged` categories;
- an explicit flag and position for whether the primary neutron itself enters the tile, retaining the meaning of the existing primary-only `hit_valid` field without mislabeling it as the whole shower.

A charged track must be counted only on its first entry into the tile so that boundary re-entry does not inflate the shower multiplicity.

#### Tile response and optical outcome

- total tile energy deposition, plus diagnostic `electron/positron`, `proton`, and `other charged` deposition components;
- existing total generated optical-photon and scintillation-photon counts;
- an explicit per-event Cerenkov-photon count;
- existing photons entering the SiPM proxy volume.

Do not treat steel energy deposition as the shower energy delivered to the tile: energy carried out of the steel by tracked secondaries is separate. Likewise, tile energy deposition and scintillation yield need not be proportional event by event because particle composition and the existing scintillator quenching model matter.

#### Zero-light handling and summary definitions

If an event creates no optical photons, its per-event collection efficiency is undefined. Store `NaN` plus a validity flag rather than assigning zero. Report zero-scintillation and zero-SiPM event fractions separately.

For every scan position and thickness, the primary run-level quantities are:

```text
production = sum(scintillation photons) / incident neutrons
collection = sum(SiPM photons) / sum(generated optical photons)
net response = sum(SiPM photons) / incident neutrons
```

The summary must also report the `16 mm / 4 mm` ratio for all three quantities, event-level mean/RMS/statistical error for tile energy deposition and photon yields, and the zero-light fractions. Do not use the mean of per-event collection ratios as the primary collection estimator.

Production runs store these fixed event aggregates, not complete step or trajectory dumps. Full process/secondary traces are limited to low-event diagnostic runs. If the Cerenkov contribution is not negligible, origin-tagged SiPM counts can be added as a later diagnostic rather than expanding the first production schema preemptively.

### D-013 — Geometry and configuration interface

**Decision:** Use a two-layer interface: keep the low-level absorber geometry minimally configurable, and lock the accepted scientific configuration behind a versioned study preset.

#### Low-level absorber geometry

Add the following `PreInit`-only, non-broadcast Geant4 messenger commands:

```text
/opnovice2/absorber/enabled true
/opnovice2/absorber/size 500 500 40 mm
```

`size` always means full `x y z` dimensions, not half-lengths. The detector defaults are `enabled=false` and `500 x 500 x 40 mm`, preserving all existing macros while making the production-size candidate the default whenever the absorber is enabled. Generated neutron-study macros must nevertheless write both commands explicitly rather than relying on those defaults.

The absorber material is the exact D-002 `StainlessSteelSAE304` definition and is not exposed as a messenger option in this first model. Likewise, do not expose independent absorber gap or position commands. The absorber is centered on the tile in `x` and `y`, and its downstream face is derived to coincide with the tile's `+Z` face. This prevents configurations that silently violate the confirmed material, thickness, or no-gap geometry while retaining the size freedom required for the D-006 convergence check.

Give the physical volume a stable `SteelAbsorber` identity and expose the constructed physical/logical volume through detector getters. D-012 interaction and energy-deposition accounting must use that volume identity rather than process or volume-name string matching.

When the absorber is enabled, retain the D-009 ordered `tile -> steel` optical border surface in addition to the `tile -> world` surface used on faces that still border the world.

#### Versioned study preset

Extend the existing scan runner with:

```text
--study-preset realistic-neutron-v1
```

The preset locks the D-002 through D-009 source, absorber, optical-surface, and SiPM baseline, including the `1 GeV/c` neutron momentum and its derived `432.58 MeV` GPS kinetic energy. Under this preset, only the intended study axes may vary:

- tile thickness: `4 mm` or `16 mm`;
- absorber transverse size for the accepted `200/300/500 mm` convergence study, while its longitudinal thickness remains `40 mm`;
- scan position/stage;
- event count and random seed.

Reject direct `--primary-energy`, material, gap, absorber-position, or manual `--beam-z` overrides under the preset. Momentum remains the authoritative source input. The generated macro must contain the resolved `/gps/particle neutron` and `/gps/energy 432.58 MeV` commands explicitly.

#### Absorber-aware source placement

Continue to center the tile at the origin and send the beam from `+Z` toward `-Z`. Derive the longitudinal positions from the resolved full tile thickness `t_tile`, absorber thickness `t_steel`, and a fixed source clearance `c = 1.5 mm`:

```text
z_tile_front     = t_tile / 2
z_steel_center   = z_tile_front + t_steel / 2
z_steel_upstream = z_tile_front + t_steel
z_source         = z_steel_upstream + c
```

The accepted resolved positions are:

| Tile thickness | Tile front | Steel center | Steel upstream face | Source Z |
| ---: | ---: | ---: | ---: | ---: |
| `4 mm` | `2 mm` | `22 mm` | `42 mm` | `43.5 mm` |
| `16 mm` | `8 mm` | `28 mm` | `48 mm` | `49.5 mm` |

Thus the source-to-tile-front distance is `41.5 mm` for both thicknesses. The `1.5 mm` clearance is a numerical separation between the virtual GPS vertex and the steel's upstream boundary; it is not a steel-to-tile air gap. No further professor clarification is needed unless a real gun-to-steel distance later becomes a scientific input. With the accepted per-axis `55 mrad` angular sigma, the nominal RMS transverse displacement over `41.5 mm` is approximately `2.28 mm` per axis.

When the absorber is enabled, the runner must use this absorber-aware calculation rather than the legacy `tile half-thickness + 1.5 mm` rule and must reject manual `--beam-z`. Scan-point `x/y` coordinates remain the GPS transverse center.

#### Validation and reproducibility metadata

Fail before event generation if dimensions are non-positive, the absorber does not cover the tile in `x/y`, any detector volume lies outside the world, the source is not strictly upstream of and outside the steel, the source lies outside the world or nominal absorber footprint, or the resolved geometry overlaps. The study preset additionally enforces the locked `40 mm` steel thickness and the D-007 `-Z` SiPM configuration. Do not invent a fixed shower-containment margin: record the nominal minimum transverse distance from all requested source positions to the absorber edge, and use the D-006 `200/300/500 mm` convergence result to establish edge insensitivity.

Extend `run_config.json` without removing existing fields. Record at least:

- a configuration-schema version and `study_preset=realistic-neutron-v1`;
- absorber enable state, box shape, stable volume identity, exact material name, density, mass fractions, and ePIC `26.07.0` reference;
- full size, center, upstream/downstream face positions, zero tile gap, and tile-contact optical-boundary model;
- requested neutron momentum and mass, derived total and kinetic energies, derivation label, GPS direction, source-clearance rule, and resolved source Z;
- point-like transverse profile, `55 mrad` angular model, and nominal minimum absorber-edge distance for the requested grid;
- runner validation results, exact generated macro paths, and the existing command and Git provenance.

The local runner owns this resolution and validation logic. E-008 remains responsible for propagating the same versioned configuration through the OSC plan, Slurm, merge, and archive workflows without redefining it.

## Current repository facts relevant to implementation

- `OpNovice2` currently uses `FTFP_BERT` together with `G4OpticalPhysics`; the existing physics list already provides neutron elastic, inelastic, and capture processes in the energy range needed here.
- The current detector geometry has no steel absorber volume.
- The primary source is backed by `G4GeneralParticleSource`, but the scan runner and metadata are still electron/Sr-90 oriented.
- The current beam convention is from `+Z` toward `-Z`; adding 4 cm of steel requires absorber-aware source placement.
- Existing output already separates generated/scintillation photon counts, collection efficiency, and photons entering the SiPM volume.

These statements describe the starting point. They are not approval of a particular implementation.

## Open engineering decisions

The following must be decided after this record is accepted and before implementation is considered complete.

### E-007 — Validation and production statistics

Define geometry visualization, one-event and low-statistics smoke tests, runtime benchmarking, convergence checks, and only then production event counts. Existing electron-study event counts must not be copied automatically to a GeV-scale neutron shower with optical tracking.

### E-008 — Local and OSC workflow

Decide how the new source and absorber parameters enter the local runner, point-plan generator, Slurm workflow, merge tools, and reproducibility metadata.

## Invariants for the next phase

- Preserve `1 GeV/c` as the authoritative neutron input and derive the GPS kinetic energy.
- Preserve 4 cm `StainlessSteelSAE304` as the absorber material/thickness reference.
- Preserve the professor-confirmed no-gap geometry and document its difference from ePIC `26.07.0`.
- Preserve the paint/wrap at the steel-facing tile surface; no air gap must not be implemented as a bare EJ-200-to-steel optical interface.
- Keep the same configurable steel transverse proxy across tile comparisons, and complete the accepted `200/300/500 mm` edge-leakage convergence check before production.
- Keep the D-007 `50 x 50 x 4/16 mm` optical baseline fixed in the first comparison.
- Label `55 mrad` as a provisional 50 mm-only choice; reopen `45/75 mrad` when the measured approximately `115 x 115 mm` groups enter scope.
- Follow the D-010 center -> one-dimensional -> two-dimensional staging, retaining 55 mrad angular divergence while the first-pass transverse source position remains point-like.
- Use the D-011 one-dimensional grid from `x = 0` through `40 mm` in `5 mm` steps at `y = 0`, plus the `x = -20 mm` symmetry check.
- Preserve the D-012 causal event record and keep neutron interaction probability, light production, optical collection, and net response as separate quantities.
- Preserve the D-013 two-layer configuration interface, absorber-aware source placement, and versioned `realistic-neutron-v1` preset; the `1.5 mm` source clearance is not a steel-to-tile gap.
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

Optical-boundary clarification:

> the tiles will still be painted/wrapped, just mean there is no air-gap between the tiles and steel.
