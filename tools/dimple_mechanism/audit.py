"""Independent issue #9 geometry and fixed-source engineering audits."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import re

import numpy as np
import uproot

from model import CUBE_SIDE_MM, RADIUS_MM, REMOVED_VOLUME_MM3, validate_configuration

_SPEC = importlib.util.spec_from_file_location(
    "_issue9_legacy_optical_audit", Path(__file__).resolve().parents[1] / "dimple_study/optical_audit.py")
legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(legacy)
require = legacy.require
close = legacy.close


def fields(line: str, prefix: str) -> dict:
    require(line.startswith(prefix), "wrong geometry diagnostic prefix")
    pairs = re.findall(r"([a-z_0-9]+)=([^\s]+)", line[len(prefix):])
    result = dict(pairs)
    require(len(result) == len(pairs), "duplicate geometry diagnostic keys")
    return result


def cavity_height(config: dict, x: float, y: float) -> float:
    if not config["enabled"]:
        return 0.
    if config["shape"] == "hemisphere":
        return math.sqrt(max(0., RADIUS_MM ** 2 - x*x-y*y))
    return CUBE_SIDE_MM if abs(x) < CUBE_SIDE_MM/2 and abs(y) < CUBE_SIDE_MM/2 else 0.


def expected_inside(config: dict, x: float, y: float, z: float) -> int:
    in_box = abs(x) < 50 and abs(y) < 50 and -12 < z < 12
    if config["shape"] == "hemisphere":
        in_front = z > 12 and x*x+y*y+(z-12)**2 < RADIUS_MM**2
        in_rear = z >= -12 and x*x+y*y+(z+12)**2 < RADIUS_MM**2
    else:
        footprint = abs(x) < CUBE_SIDE_MM/2 and abs(y) < CUBE_SIDE_MM/2
        in_front = footprint and 12 <= z < 12+CUBE_SIDE_MM
        in_rear = footprint and -12 <= z < -12+CUBE_SIDE_MM
    inside = (in_box or (config["front"] and in_front)) and not (config["enabled"] and in_rear)
    return 2 if inside else 0


def audit_geometry(log: str, config: dict) -> dict:
    validate_configuration(config)
    shape = "G4SubtractionSolid" if config["enabled"] else "G4Box"
    require("Geometry overlaps check completed !" in log and
            f"Checking overlaps for volume Tank:0 ({shape}) ... OK!" in log and
            "Checking overlaps for volume SiPM:0 (G4Box) ... OK!" in log,
            "geometry overlap checks incomplete")
    require(not re.search(r"Overlap is detected|GeomVol1002|overlap with", log), "geometry overlap detected")
    positions = re.findall(r"SiPM placement: layout=single, layer=0, local_sensor=0, copy=0, "
                           r"face=-Z, local=\(([^)]+)\) mm, world=\(([^)]+)\) mm", log)
    sizes = re.findall(r"SiPM geometry: full_size=\(([^)]+)\) mm", log)
    require(len(positions) == len(sizes) == log.count("SiPM placement:") == 1,
            "missing/ambiguous actual SiPM geometry")
    for actual, expected in ((positions[0][0], (0.,0.,0.)), (positions[0][1], (0.,0.,-12.25)),
                             (sizes[0], (config["sensor_mm"], config["sensor_mm"], .5))):
        values = [float(item) for item in actual.split(",")]
        require(len(values) == 3, "malformed actual geometry vector")
        close(values, expected, "wrong actual SiPM dimensions/position")
    summaries = [fields(line, "Issue9 geometry:") for line in log.splitlines() if line.startswith("Issue9 geometry:")]
    require(len(summaries) == 1, "missing/duplicate actual solid identity")
    summary = summaries[0]
    require(summary["shape"] == (config["shape"] if config["enabled"] else "flat"), "wrong actual solid shape")
    require(summary["solid_type"] == shape, "wrong actual Geant4 solid class")
    volume = REMOVED_VOLUME_MM3 if config["enabled"] else 0.
    front_extent = (RADIUS_MM if config["shape"] == "hemisphere" else CUBE_SIDE_MM) if config["front"] else 0.
    expected = dict(front_compensation=int(config["front"]), reference_radius_mm=RADIUS_MM,
                    cube_side_mm=CUBE_SIDE_MM, removed_volume_mm3=volume,
                    restored_volume_mm3=volume if config["front"] else 0.,
                    nominal_volume_mm3=240000.,
                    analytic_scintillator_volume_mm3=240000.-volume+(volume if config["front"] else 0.),
                    front_extent_mm=front_extent, receiving_plane_z_mm=-12.)
    for name, value in expected.items():
        close(float(summary[name]), value, f"wrong actual geometry identity: {name}")
    rays = [fields(line, "Issue9 ray:") for line in log.splitlines() if line.startswith("Issue9 ray:")]
    probes = [fields(line, "Issue9 inside:") for line in log.splitlines() if line.startswith("Issue9 inside:")]
    require(len(rays) >= 3 and len(probes) >= 3, "insufficient actual solid probes")
    require(len({row["label"] for row in rays}) == len(rays), "duplicate ray labels")
    require(len({row["label"] for row in probes}) == len(probes), "duplicate inside labels")
    for row in rays:
        x, y = float(row["x_mm"]), float(row["y_mm"])
        height = cavity_height(config, x, y)
        entry, exit_z = 12+(height if config["front"] else 0.), -12+height
        for key, value in (("entry_z_mm", entry), ("exit_z_mm", exit_z), ("thickness_mm", entry-exit_z)):
            close(float(row[key]), value, f"actual solid ray failed: {row['label']} {key}")
    require(any(float(row["x_mm"]) == float(row["y_mm"]) == 0 for row in rays), "missing center ray")
    require(any(abs(float(row["x_mm"])) > 3 or abs(float(row["y_mm"])) > 3 for row in rays), "missing outside-cavity ray")
    for row in probes:
        xyz = [float(row[key]) for key in ("x_mm", "y_mm", "z_mm")]
        require(int(row["observed"]) == expected_inside(config, *xyz), f"actual solid Inside failed: {row['label']}")
    endings = re.findall(r"^Issue9 geometry audit: PASS rays=(\d+) inside_probes=(\d+)\s*$", log, re.M)
    require(endings == [(str(len(rays)), str(len(probes)))], "actual solid probe completion mismatch")
    return dict(status="passed", rays=len(rays), inside_probes=len(probes), solid_type=shape,
                nominal_volume_mm3=expected["nominal_volume_mm3"], sensor_size_mm=config["sensor_mm"])


def audit_scan(scan: dict, case: dict) -> dict:
    # The old audit assumes z=4 mm. Verify the real source first, then reuse only
    # its source-independent integer/count/ratio invariants on a copied view.
    required = {"electron_tile_edep_mev", "scint_centroid_valid", "scintillation_photons",
                "scint_centroid_x_mm", "scint_centroid_y_mm", "scint_centroid_z_mm", "shoot_z_mm"}
    require(required <= set(scan), "missing source/energy/centroid fields")
    close(scan["shoot_z_mm"], 18., "wrong source shoot_z_mm: expected 18 mm")
    energy = scan["electron_tile_edep_mev"]
    require(np.all(np.isfinite(energy)) and np.all(energy >= 0), "invalid electron/positron tile energy")
    valid = scan["scintillation_photons"] > 0
    require(np.array_equal(scan["scint_centroid_valid"], valid.astype(int)), "wrong scintillation centroid validity")
    for axis in "xyz":
        require(np.all(np.isfinite(scan[f"scint_centroid_{axis}_mm"][valid])), "invalid scintillation birth centroid")
    projected = dict(scan)
    projected["shoot_z_mm"] = np.full_like(scan["shoot_z_mm"], 4.)
    report = legacy.audit_scan(projected, dict(events=case["events"], x_mm=case["x_mm"], require_response=False))
    report["electron_positron_tile_edep_sum_mev"] = float(energy.sum())
    report["scint_centroid_valid_events"] = int(valid.sum())
    return report


def audit_case(directory: Path, case: dict, process_exit: int) -> dict:
    require(process_exit == 0, f"process exited {process_exit}")
    require(case["stage"] == "smoke" and case["events"] == 2, "not a fixed engineering smoke case")
    log = legacy.audit_log(directory/"simulation.log")
    require(len(re.findall(r"^ISSUE9_SMOKE_DONE\s*$", log, re.M)) == 1, "missing/duplicate smoke completion")
    require(re.findall(r"^Number of events:\s*(\d+)\s*$", log, re.M) == [str(case["events"])], "wrong event count")
    require(re.search(r"Primary particle was:\s*e-\s+with energy\s+1(?:\.0*)?\s+MeV\.", log), "wrong primary log")
    require("Material of world: G4_AIR" in log and
            re.search(r"Material of tank:\s+G4_PLASTIC_SC_VINYLTOLUENE", log), "wrong materials")
    geometry = audit_geometry(log, case["config"])
    with uproot.open(directory/"result.root") as root:
        require("scan" in root, "missing scan tree")
        scan = legacy.table_arrays(root["scan"])
        report = audit_scan(scan, case)
        for name in legacy.EMPTY_TREES:
            require(name in root and root[name].num_entries == 0, f"{name} must exist and remain empty")
        require(legacy.DIAGNOSTIC_TREE in root, "missing passive optical diagnostic")
        diagnostics = legacy.audit_diagnostics(legacy.table_arrays(root[legacy.DIAGNOSTIC_TREE]), scan, case)
    legacy.audit_summary(directory/"result_summary.csv", scan, report)
    rng = directory/"result.rng"
    legacy.read_rng_state(rng)
    return dict(status="passed", geometry=geometry, **report, diagnostics=diagnostics,
                root_sha256=legacy.digest(directory/"result.root"),
                summary_sha256=legacy.digest(directory/"result_summary.csv"), rng_sha256=legacy.digest(rng),
                zero_response_events=int(np.count_nonzero(scan["sipm_detected_photons"] == 0)),
                scope="Two-event engineering sample; no effect size, precision or ranking claim")
