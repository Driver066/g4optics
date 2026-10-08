#!/usr/bin/env python3
"""Engineering checks for the dimple optical sample and passive diagnostics."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
from pathlib import Path
import re

import numpy as np
import uproot


# Load the existing warning policy without importing an ambiguous `audit` module.
_SPEC = importlib.util.spec_from_file_location(
    "_dimple_layout_log_audit", Path(__file__).resolve().parents[1] / "layout_study/audit.py")
_LAYOUT = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_LAYOUT)
audit_log = _LAYOUT.audit_log
require = _LAYOUT.require

DIAGNOSTIC_TREE = "dimple_optical_event_v1"
SENSORS = tuple(f"sipm_sensor_{i}_detected_photons" for i in range(4))
COUNTS = ("generated_optical_photons", "scintillation_photons",
          "cerenkov_photons", "sipm_detected_photons", *SENSORS)
SCAN_REQUIRED = {"event_id", "shoot_x_mm", "shoot_y_mm", "shoot_z_mm",
                 "primary_kinetic_energy_mev", "collection_efficiency",
                 "collection_efficiency_valid", "steel_edep_mev", "tile_edep_mev", *COUNTS}
EMPTY_TREES = ("decay_betas", "stack_layers", "stack_transfers",
               "layout_layers", "layout_sensors", "layout_transfers")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_arrays(tree) -> dict:
    values = tree.arrays(library="np")
    return values if isinstance(values, dict) else {name: values[name] for name in values.dtype.names}


def same_values(left, right) -> bool:
    left, right = np.asarray(left), np.asarray(right)
    if left.dtype != right.dtype or left.shape != right.shape:
        return False
    if np.issubdtype(left.dtype, np.inexact):
        return bool(np.array_equal(left, right, equal_nan=True))
    return bool(np.array_equal(left, right))


def event_order(table: dict, events: int, name: str) -> np.ndarray:
    require("event_id" in table, f"missing event_id in {name}")
    require(all(len(values) == events for values in table.values()), f"{name} row count mismatch")
    ids = table["event_id"]
    require(np.issubdtype(ids.dtype, np.integer), f"noninteger event_id in {name}")
    order = np.argsort(ids)
    require(np.array_equal(ids[order], np.arange(events)), f"duplicate/missing event IDs in {name}")
    return order


def close(actual, expected, message: str) -> None:
    require(np.all(np.isclose(actual, expected, rtol=1e-10, atol=1e-9, equal_nan=True)), message)


def audit_scan(scan: dict, config: dict) -> dict:
    events = config["events"]
    require(SCAN_REQUIRED <= set(scan), f"missing scan columns: {sorted(SCAN_REQUIRED-set(scan))}")
    event_order(scan, events, "scan")
    for name in COUNTS:
        values = scan[name]
        require(np.issubdtype(values.dtype, np.integer), f"noninteger {name}")
        require(np.all(values >= 0), f"negative {name}")
    for name in ("steel_edep_mev", "tile_edep_mev"):
        require(np.all(np.isfinite(scan[name])) and np.all(scan[name] >= 0), f"invalid {name}")
    for name, expected in (("shoot_x_mm", config["x_mm"]), ("shoot_y_mm", 0.),
                           ("shoot_z_mm", 4.), ("primary_kinetic_energy_mev", 1.)):
        close(scan[name], expected, f"wrong source {name}")
    generated = scan["generated_optical_photons"].astype(np.int64)
    scint = scan["scintillation_photons"].astype(np.int64)
    cerenkov = scan["cerenkov_photons"].astype(np.int64)
    detected = scan["sipm_detected_photons"].astype(np.int64)
    require(np.array_equal(generated, scint + cerenkov), "G != scintillation + Cerenkov")
    require(np.all(cerenkov == 0), "Cerenkov must be zero in this sample")
    require(np.array_equal(detected, sum(scan[name].astype(np.int64) for name in SENSORS)),
            "D != sum of four sensor slots")
    require(all(np.all(scan[name] == 0) for name in SENSORS[1:]), "inactive sensor has detections")
    require(np.all(scan["steel_edep_mev"] == 0), "steel energy present in single-tile sample")
    valid = generated > 0
    require(np.issubdtype(scan["collection_efficiency_valid"].dtype, np.integer),
            "noninteger efficiency validity")
    require(np.array_equal(scan["collection_efficiency_valid"], valid.astype(np.int32)),
            "efficiency validity mismatch")
    expected_ratio = np.full(events, np.nan)
    np.divide(detected, generated, out=expected_ratio, where=valid)
    close(scan["collection_efficiency"], expected_ratio, "collection efficiency != D/G")
    total_g, total_d = int(generated.sum()), int(detected.sum())
    require(total_g > 0 and total_d > 0, "sample did not exercise optical response")
    return {"events": events, "generated_photons": total_g, "detected_photons": total_d,
            "scintillation_photons": int(scint.sum()), "cerenkov_photons": int(cerenkov.sum()),
            "collection_efficiency": total_d / total_g,
            "tile_edep_sum_mev": float(scan["tile_edep_mev"].sum())}


def audit_summary(path: Path, scan: dict, report: dict) -> None:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    require(len(rows) == 1, "summary must contain exactly one row")
    summary, events = rows[0], report["events"]
    expected = {
        "events": events, "committed_events": events, "shoot_position_events": events,
        "primary_energy_events": events, "generated_optical_photons": report["generated_photons"],
        "scintillation_photons": report["scintillation_photons"],
        "cerenkov_photons": 0, "sipm_detected_photons": report["detected_photons"],
        "collection_efficiency_valid": 1, "collection_efficiency": report["collection_efficiency"],
        "production_scint_photons_per_event": report["scintillation_photons"] / events,
        "net_sipm_photons_per_event": report["detected_photons"] / events,
        "steel_edep_sum_mev": 0., "tile_edep_sum_mev": report["tile_edep_sum_mev"],
        "primary_energy_mean_mev": 1., "primary_energy_rms_mev": 0.,
        "primary_energy_min_mev": 1., "primary_energy_max_mev": 1.,
    }
    for axis in "xyz":
        expected[f"shoot_{axis}_mm"] = float(scan[f"shoot_{axis}_mm"].mean())
    for prefix, column in (("generated_optical", "generated_optical_photons"),
                           ("scintillation", "scintillation_photons"),
                           ("sipm_detected", "sipm_detected_photons")):
        values = scan[column].astype(np.float64)
        mean = float(values.mean())
        rms = float(np.sqrt(max(0., float(np.mean(values * values)) - mean * mean)))
        expected.update({f"{prefix}_mean": mean, f"{prefix}_rms": rms,
                         f"{prefix}_se": rms / np.sqrt(events),
                         f"{prefix}_zero_events": int(np.count_nonzero(values == 0)),
                         f"{prefix}_zero_fraction": float(np.mean(values == 0))})
    require(set(expected) <= set(summary), f"missing summary fields: {sorted(set(expected)-set(summary))}")
    for field, value in expected.items():
        if isinstance(value, int):
            require(re.fullmatch(r"\d+", summary[field]) is not None and int(summary[field]) == value,
                    f"summary {field} mismatch")
        else:
            close(float(summary[field]), value, f"summary {field} mismatch")


def audit_geometry(log: str, geometry: str) -> None:
    tank_shape = "G4SubtractionSolid" if geometry == "dimple" else "G4Box"
    require("Geometry overlaps check completed !" in log and
            f"Checking overlaps for volume Tank:0 ({tank_shape}) ... OK!" in log and
            "Checking overlaps for volume SiPM:0 (G4Box) ... OK!" in log,
            "geometry overlap checks incomplete")
    positions = re.findall(r"SiPM placement: layout=single, layer=0, local_sensor=0, copy=0, "
                           r"face=-Z, local=\(([^)]+)\) mm, world=\(([^)]+)\) mm", log)
    sizes = re.findall(r"SiPM geometry: full_size=\(([^)]+)\) mm", log)
    require(len(positions) == len(sizes) == 1, "missing/ambiguous actual SiPM geometry")
    require(len(re.findall(r"SiPM placement:", log)) == 1, "unexpected additional SiPM placement")
    for actual, expected in ((positions[0][0], (0., 0., 0.)),
                             (positions[0][1], (0., 0., -2.75)),
                             (sizes[0], (6., 6., .5))):
        values = [float(x) for x in actual.split(",")]
        require(len(values) == 3, "malformed geometry vector")
        close(values, expected, "wrong actual SiPM geometry")
    identity = "Dimple geometry: shape=hemisphere, radius=3 mm, sipm_mode=external, opening_z=-2.5 mm"
    if geometry == "dimple":
        require(log.count("Dimple geometry:") == 1 and identity in log, "wrong dimple geometry")
    else:
        require("Dimple geometry:" not in log, "flat case constructed a dimple")


def audit_diagnostics(table: dict, scan: dict, config: dict) -> dict:
    births = tuple(f"{parent}_parent_{process}_births"
                   for parent in ("nonoptical", "optical")
                   for process in ("scintillation", "cerenkov", "other"))
    counters = ("event_aborted", "legacy_generated", "legacy_detected", *births,
                "all_optical_births", "optical_primary_tracks", "optical_tracks_started",
                "optical_tracking_resumes", "sipm_detection_calls", "sipm_unique_detected_tracks",
                "duplicate_sipm_detections", "unregistered_sipm_detections", "primary_count", "primary_pdg")
    required = {"event_id", *counters, "primary_dir_x", "primary_dir_y", "primary_dir_z",
                "primary_kinetic_energy_mev"}
    require(required <= set(table), f"missing diagnostic columns: {sorted(required-set(table))}")
    order = event_order(table, config["events"], DIAGNOSTIC_TREE)
    diag = {name: values[order] for name, values in table.items()}
    scan_order = event_order(scan, config["events"], "scan")
    for name in counters:
        require(np.issubdtype(diag[name].dtype, np.integer), f"noninteger diagnostic {name}")
        require(np.all(diag[name] >= 0), f"negative diagnostic {name}")
    zero_fields = ("event_aborted", "nonoptical_parent_cerenkov_births",
                   "optical_parent_cerenkov_births", "nonoptical_parent_other_births",
                   "optical_parent_other_births", "optical_primary_tracks",
                   "duplicate_sipm_detections", "unregistered_sipm_detections")
    for name in zero_fields:
        require(np.all(diag[name] == 0), f"nonzero diagnostic {name}")
    for name, expected in (("primary_count", 1), ("primary_pdg", 11),
                           ("primary_dir_x", 0.), ("primary_dir_y", 0.),
                           ("primary_dir_z", -1.), ("primary_kinetic_energy_mev", 1.)):
        close(diag[name], expected, f"wrong diagnostic source {name}")
    g = scan["generated_optical_photons"][scan_order]
    d = scan["sipm_detected_photons"][scan_order]
    identities = (
        (diag["legacy_generated"], g, "diagnostic/scan G"),
        (diag["legacy_detected"], d, "diagnostic/scan D"),
        (diag["nonoptical_parent_scintillation_births"], g, "nonoptical-parent births/legacy G"),
        (sum(diag[name].astype(np.int64) for name in births), diag["all_optical_births"], "birth categories/all births"),
        (g.astype(np.int64) + diag["optical_parent_scintillation_births"], diag["all_optical_births"], "G plus optical-parent births/all births"),
        (diag["all_optical_births"].astype(np.int64) + diag["optical_primary_tracks"], diag["optical_tracks_started"], "births plus optical primaries/unique starts"),
        (diag["sipm_detection_calls"], d, "detection calls/legacy D"),
        (diag["sipm_unique_detected_tracks"], d, "unique detected tracks/legacy D"),
    )
    for actual, expected, label in identities:
        require(np.array_equal(actual, expected), f"diagnostic closure mismatch: {label}")
    require(np.all(d <= diag["all_optical_births"]), "detected tracks exceed all optical births")
    require(np.all(d <= diag["optical_tracks_started"]), "detected tracks exceed unique starts")
    return {"status": "passed", "schema": DIAGNOSTIC_TREE,
            **{name: int(diag[name].astype(np.int64).sum()) for name in counters
               if name not in ("primary_count", "primary_pdg")},
            "source_direction_verified": True,
            "scope": "birth/start and unique detection accounting; no terminal-fate ledger; legacy denominator preserved"}


def audit_case(directory: Path, config: dict, process_exit: int) -> dict:
    directory = Path(directory)
    require(process_exit == 0, f"process exited {process_exit}")
    require(isinstance(config["events"], int) and config["events"] > 0, "events must be positive")
    require(config["geometry"] in ("flat", "dimple"), "unknown geometry")
    require(np.isfinite(config["x_mm"]), "source x must be finite")
    log_path, root_path = directory / "simulation.log", directory / "result.root"
    summary_path = directory / "result_summary.csv"
    log = audit_log(log_path)
    require(len(re.findall(r"^DIMPLE_OPTICAL_DONE\s*$", log, re.M)) == 1,
            "missing/ambiguous DIMPLE_OPTICAL_DONE completion marker")
    require(re.search(r"Primary particle was:\s*e-\s+with energy\s+1(?:\.0*)?\s+MeV\.", log),
            "log primary must be a fixed 1 MeV electron")
    require(re.findall(r"^Number of events:\s*(\d+)\s*$", log, re.M) == [str(config["events"])],
            "log event count mismatch or missing")
    require("Material of world: G4_AIR" in log and
            re.search(r"Material of tank:\s+G4_PLASTIC_SC_VINYLTOLUENE", log),
            "wrong logged materials")
    audit_geometry(log, config["geometry"])
    with uproot.open(root_path) as root:
        require("scan" in root, "missing scan tree")
        scan = table_arrays(root["scan"])
        report = audit_scan(scan, config)
        for name in EMPTY_TREES:
            require(name in root and root[name].num_entries == 0, f"{name} must exist and remain empty")
        has_diagnostics = DIAGNOSTIC_TREE in root
        require(has_diagnostics == bool(config["diagnostics"]), "diagnostic tree presence mismatch")
        if has_diagnostics:
            report["diagnostics"] = audit_diagnostics(table_arrays(root[DIAGNOSTIC_TREE]), scan, config)
    audit_summary(summary_path, scan, report)
    report.update(status="passed", geometry=config["geometry"], x_mm=config["x_mm"],
                  diagnostics_enabled=bool(config["diagnostics"]), exit_code=process_exit,
                  source_direction_verified=bool(config["diagnostics"]),
                  root_sha256=digest(root_path), summary_sha256=digest(summary_path),
                  log_sha256=digest(log_path), scope="engineering sample; no scientific precision claim")
    if config.get("variant") != "reference":
        state = read_rng_state(directory / "result.rng")
        report["rng_state_sha256"] = hashlib.sha256(state).hexdigest()
        report["rng_state_header"] = state.splitlines()[0].decode("ascii")
    return report


def read_rng_state(path: Path) -> bytes:
    require(path.is_file(), f"missing RNG state file: {path}")
    state = path.read_bytes()
    require(state.strip(), "empty RNG state file")
    # This format was observed with saveFullState in the frozen Geant4 image:
    # MixMaxRng-begin 1, integer state lines, and MixMaxRng-end. It has no Uvec.
    lines = state.splitlines()
    require(len(lines) >= 4 and re.fullmatch(rb"MixMaxRng-begin \d+", lines[0]) is not None
            and lines[-1] == b"MixMaxRng-end", "missing CLHEP MixMaxRng state delimiters")
    require(all(re.fullmatch(rb"[+-]?\d+", line.strip()) is not None for line in lines[1:-1]),
            "missing CLHEP RNG state sequence")
    return state


def compare_legacy(ref_dir: Path, candidate_dir: Path, require_rng: bool = True) -> dict:
    ref_dir, candidate_dir = Path(ref_dir), Path(candidate_dir)
    tree_count = branch_count = histogram_count = 0
    with uproot.open(ref_dir / "result.root") as reference, \
            uproot.open(candidate_dir / "result.root") as candidate:
        ref_classes = {k: v for k, v in reference.classnames(cycle=False).items() if k != DIAGNOSTIC_TREE}
        new_classes = {k: v for k, v in candidate.classnames(cycle=False).items() if k != DIAGNOSTIC_TREE}
        require(ref_classes == new_classes, "legacy ROOT object names/classes differ")
        for name, classname in ref_classes.items():
            left, right = reference[name], candidate[name]
            if classname in ("TTree", "ROOT::RNTuple"):
                left_arrays, right_arrays = table_arrays(left), table_arrays(right)
                require(set(left_arrays) == set(right_arrays), f"legacy tree branch names differ: {name}")
                for branch in left_arrays:
                    require(same_values(left_arrays[branch], right_arrays[branch]),
                            f"legacy tree branch differs: {name}/{branch}")
                    branch_count += 1
                tree_count += 1
            elif classname.startswith("TH1"):
                for label, a, b in (("bins/flow", left.values(flow=True), right.values(flow=True)),
                                    ("errors/flow", left.errors(flow=True), right.errors(flow=True)),
                                    ("edges", left.axis().edges(flow=True), right.axis().edges(flow=True))):
                    require(same_values(a, b), f"legacy histogram {label} differs: {name}")
                histogram_count += 1
            else:
                require(classname.startswith("TDirectory"), f"unhandled legacy ROOT object: {name} ({classname})")
    require(tree_count > 0, "no legacy ROOT trees compared")
    ref_summary = (ref_dir / "result_summary.csv").read_bytes()
    require(ref_summary == (candidate_dir / "result_summary.csv").read_bytes(), "legacy summary bytes differ")
    report = {"status": "passed", "legacy_trees_compared": tree_count,
              "legacy_branches_compared": branch_count, "histograms_compared": histogram_count,
              "summary_bytes_equal": True, "rng_compared": require_rng,
              "scope": "legacy ROOT content and summary bytes; serialization metadata excluded"}
    if require_rng:
        ref_rng = read_rng_state(ref_dir / "result.rng")
        require(ref_rng == read_rng_state(candidate_dir / "result.rng"), "RNG state bytes differ")
        report.update(rng_state_bytes_equal=True, rng_state_sha256=hashlib.sha256(ref_rng).hexdigest(),
                      rng_state_header=ref_rng.splitlines()[0].decode("ascii"))
    return report
