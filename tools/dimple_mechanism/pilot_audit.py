"""Audit registered 100-event pilot outputs; never launches simulation."""
from __future__ import annotations

from pathlib import Path
import re

import numpy as np
import uproot

import audit as engineering_audit
import pilot_model

legacy = engineering_audit.legacy
require = legacy.require
close = legacy.close


def audit_scan(scan: dict, task: dict) -> dict:
    pilot_model.validate_task(task)
    required = {"shoot_x_mm", "shoot_y_mm", "shoot_z_mm", "scint_centroid_valid"}
    require(required <= set(scan), "missing pilot source/centroid columns")
    # Check the real coordinates before using the previous zero-y audit's
    # source-independent counter and summary conventions on a copied view.
    for axis, expected in (("x", task["x_mm"]), ("y", task["y_mm"]), ("z", pilot_model.SOURCE_Z_MM)):
        close(scan[f"shoot_{axis}_mm"], expected, f"wrong pilot source shoot_{axis}_mm")
    require(np.issubdtype(scan["scint_centroid_valid"].dtype, np.integer), "noninteger scintillation centroid validity")
    projected = dict(scan)
    projected["shoot_y_mm"] = np.zeros_like(scan["shoot_y_mm"])
    report = engineering_audit.audit_scan(projected, task)
    report["zero_generated_events"] = int(np.count_nonzero(scan["generated_optical_photons"] == 0))
    report["zero_response_events"] = int(np.count_nonzero(scan["sipm_detected_photons"] == 0))
    return report


def audit_log(log_path: Path, task: dict, process_exit: int) -> tuple[str, dict]:
    pilot_model.validate_task(task)
    require(type(process_exit) is int and process_exit == 0, f"pilot process exited {process_exit}")
    log = legacy.audit_log(log_path)
    require(len(re.findall(r"^ISSUE9_PILOT_DONE[ \t]*\r?$", log, re.M)) == 1,
            "missing/duplicate ISSUE9_PILOT_DONE completion marker")
    require(re.findall(r"^Number of events:[ \t]*(\d+)[ \t]*\r?$", log, re.M) == [str(task["events"])],
            "pilot log event count mismatch")
    require(re.search(r"Primary particle was:\s*e-\s+with energy\s+1(?:\.0*)?\s+MeV\.", log),
            "pilot log primary must be a fixed 1 MeV electron")
    require("Material of world: G4_AIR" in log and
            re.search(r"Material of tank:\s+G4_PLASTIC_SC_VINYLTOLUENE", log), "wrong pilot materials")
    return log, engineering_audit.audit_geometry(log, task["config"])


def audit_case(directory: Path, task: dict, process_exit: int) -> dict:
    directory = Path(directory)
    _, geometry = audit_log(directory/"simulation.log", task, process_exit)
    root_path = directory/"result.root"
    with uproot.open(root_path) as root:
        require("scan" in root, "missing pilot scan tree")
        scan = legacy.table_arrays(root["scan"])
        report = audit_scan(scan, task)
        for name in legacy.EMPTY_TREES:
            require(name in root and root[name].num_entries == 0, f"{name} must exist and remain empty")
        require(legacy.DIAGNOSTIC_TREE in root, "missing pilot optical diagnostic tree")
        diagnostics = legacy.audit_diagnostics(legacy.table_arrays(root[legacy.DIAGNOSTIC_TREE]), scan, task)
    # The original, unmodified source arrays are used for CSV reconciliation.
    summary_path = directory/"result_summary.csv"
    legacy.audit_summary(summary_path, scan, report)
    rng_path = directory/"result.rng"
    legacy.read_rng_state(rng_path)
    return dict(status="passed", accepted=True, model_id=pilot_model.MODEL_ID, task_id=task["task_id"],
                configuration=task["configuration"], point_id=task["point_id"], block=task["block"],
                x_mm=task["x_mm"], y_mm=task["y_mm"], source_z_mm=pilot_model.SOURCE_Z_MM,
                geometry=geometry, **report, diagnostics=diagnostics,
                root_sha256=legacy.digest(root_path), summary_sha256=legacy.digest(summary_path),
                log_sha256=legacy.digest(directory/"simulation.log"), rng_sha256=legacy.digest(rng_path),
                scope="Registered pilot sample; all events retained, including zero response and undefined D/G")
