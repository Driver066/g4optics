"""Verified OSC calibration receipts; never reinterpret engineering data as science."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import sys

import numpy as np
import uproot

from audit import audit_run, one, tree_arrays
from benchmark_audit import numerical_trace
from model import canonical_json, configuration_hash, validate_tasks
from osc import read, sha256, require
from precision_math import SPEC

PURPOSE = "sample-size-calibration-only"
CONTROLLER_FILES = ("precision.py", "precision_math.py", "precision_io.py", "precision_worker.py",
                    "osc.py", "model.py", "audit.py", "benchmark_audit.py")
IDENTITIES = ("source_manifest_sha256", "executable_sha256", "sif_sha256", "dataset_receipt_sha256")


def save_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def analysis_identity():
    return dict(python=platform.python_version(), interpreter_sha256=sha256(Path(sys.executable).resolve()),
                packages={d.metadata["Name"].lower().replace("_", "-"): d.version
                          for d in importlib.metadata.distributions()})


def verify_controller(control, *, files=CONTROLLER_FILES):
    root = Path(control["root"])
    document_path = root/"controller.json"
    require(sha256(document_path) == control["sha256"], "Frozen controller manifest changed")
    document = read(document_path)
    actual = {}
    for p in root.rglob("*"):
        require(not p.is_symlink(), "Symlink in controller snapshot")
        if p.is_file() and p.name != "controller.json" and "__pycache__" not in p.parts:
            actual[p.relative_to(root).as_posix()] = sha256(p)
    require(actual == document["files"] and set(actual) == set(files), "Controller inventory changed")
    require(analysis_identity() == document["analysis_identity"], "Analysis interpreter/dependencies changed")
    return document


def read_campaign(bundle, manifest_hash):
    bundle = Path(bundle).resolve()
    require(sha256(bundle/"manifest.json") == manifest_hash, "Calibration manifest changed")
    m = read(bundle/"manifest.json")
    require(m["purpose"] == "calibration" and m["mock"] is False, "Wrong calibration purpose or mock build")
    require(m["optical_numerics"] == {"profile":"painted-corner-v2", "scale":16}, "Wrong numerical baseline")
    control = m["calibration_control"]
    require(Path(control["root"]).resolve() == bundle.parent/"controller", "Controller outside campaign")
    require(Path(control["stop_file"]).resolve() == bundle.parent/"STOP.json", "Stop marker outside campaign")
    require(Path(m["remote_output_root"]).resolve() == bundle.parent/"results", "Results outside campaign")
    require(read(bundle.parent/"statistics-spec.json") == SPEC and
            sha256(bundle.parent/"statistics-spec.json") == control["spec_sha256"], "Statistical specification changed")
    require(m["precision_spec"] == SPEC, "Manifest statistical specification differs")
    require(sha256(bundle.parent/"excluded-benchmark-seeds.json") == control["excluded_seeds_sha256"],
            "Excluded seed registry changed")
    evidence = bundle.parent/"prior-evidence"
    require(sha256(evidence/"SHA256.json") == control["prior_evidence_sha256"], "Prior evidence index changed")
    for name, expected in read(evidence/"SHA256.json").items():
        require(Path(name).name == name and sha256(evidence/name) == expected, "Prior evidence changed")
    verify_controller(control)
    require(sha256(bundle/"array_task.py") == m["worker_sha256"], "Execution worker changed")
    require(sha256(bundle/"tasks.json") == m["tasks_sha256"], "Calibration registry changed")
    tasks = read(bundle/"tasks.json")
    require(validate_tasks(tasks, total_event_budget=8000) == m["summary"], "Calibration registry summary mismatch")
    require(len(tasks) == 80 and sum(t["events"] for t in tasks) == 8000, "Incomplete calibration matrix")
    groups = {}
    for task in tasks:
        require(task["stage"] == "calibration" and task["purpose"] == PURPOSE and not task.get("repeat_of"),
                "Benchmark/repeat/scientific sample mixed into calibration")
        require(task["events"] == 100 and task["campaign_seed"] == SPEC["calibration_seed"], "Calibration size/seed policy changed")
        cfg = task["config"]
        key = (cfg["tile_thickness_mm"], cfg["layout"], cfg["gap_mm"])
        groups.setdefault(key, []).append(task["block"])
    expected = {(t,l,g) for t,l in SPEC["pairs"] for g in (.5,1.)}
    require(set(groups) == expected and all(sorted(v) == list(range(10)) for v in groups.values()),
            "Missing/duplicate configuration or independent block")
    return m, tasks


def audit_execution(m, task, index, receipt_path, manifest_hash):
    receipt_path = Path(receipt_path).resolve()
    e = read(receipt_path)
    require(e["status"] == "execution-complete-not-audited" and e["exit_code"] == 0, "Unfinished calibration execution")
    require(e["task_id"] == task["task_id"] and e["events"] == task["events"] and
            e["array_index"] == index and e["purpose"] == PURPOSE and
            e["optical_numerics"] == task["config"]["optical_numerics"], "Execution task identity mismatch")
    require(e["manifest_sha256"] == manifest_hash and all(e[k] == m[k] for k in IDENTITIES),
            "Execution simulation identity mismatch")
    expected_parent = Path(m["remote_output_root"])/task["task_id"]/("job-"+e["scheduler_job"]+"-task-"+str(index))
    require(receipt_path.parent == expected_parent.resolve(), "Wrong execution attempt path")
    run = Path(e["run_dir"]).resolve()
    require(receipt_path.parent in run.parents, "Run path outside task attempt")
    actual = {}
    for p in run.rglob("*"):
        require(not p.is_symlink(), "Symlink in calibration output")
        if p.is_file():
            actual[p.relative_to(run).as_posix()] = sha256(p)
    require(actual and actual == e["artifacts"], "Output inventory/checksum changed")
    result = audit_run(run, task["config"], task["events"], expected_seeds=(task["seed1"],task["seed2"]))
    require(result["passed"] is True, "Calibration ledger audit failed")
    numeric = numerical_trace(one((run/"outputs").glob("*.optical-numerics.jsonl"), "numerical trace"),
                              task["config"], task["events"])
    return dict(passed=True, purpose=PURPOSE, accepted_statistical_evidence=False,
        task_id=task["task_id"], events=task["events"], configuration_hash=task["configuration_hash"],
        manifest_sha256=manifest_hash, execution_sha256=sha256(receipt_path),
        execution=receipt_path.as_posix(), run_dir=run.as_posix(),
        controller_sha256=m["calibration_control"]["sha256"], audit=result, numerical=numeric)


def accepted_index(bundle, manifest_hash):
    m, tasks = read_campaign(bundle, manifest_hash)
    require(not Path(m["calibration_control"]["stop_file"]).exists(), "Calibration has a STOP marker")
    root = Path(m["remote_output_root"])
    require({p.name for p in root.iterdir() if p.is_dir()} == {t["task_id"] for t in tasks},
            "Missing/unregistered calibration task directory")
    entries = []
    for index, task in enumerate(tasks, 1):
        attempts = list((root/task["task_id"]).glob("job-*"))
        require(len(attempts) == 1, "Missing/ambiguous attempt: "+task["task_id"])
        attempt = attempts[0]
        require(not (attempt/"failure.json").exists(), "Task has a failure record")
        receipt_path = attempt/"accepted.json"
        receipt = read(receipt_path)
        require(receipt["accepted_for_calibration"] is True and receipt["accepted_for_main"] is False and
                receipt["manifest_sha256"] == manifest_hash and receipt["task_id"] == task["task_id"] and
                receipt["registered_task_sha256"] == hashlib.sha256(canonical_json(task)).hexdigest(),
                "Accepted receipt identity mismatch")
        audit_path = attempt/"calibration-audit.json"
        require(sha256(audit_path) == receipt["audit_sha256"] and
                sha256(attempt/"execution.json") == receipt["execution_sha256"], "Accepted receipt checksum mismatch")
        verified = audit_execution(m, task, index, attempt/"execution.json", manifest_hash)
        require(verified == read(audit_path), "Revalidated calibration audit differs")
        path = one((Path(verified["run_dir"])/"outputs").glob("*.root"), "calibration ROOT")
        entries.append(dict(task=task, root=path.as_posix(), root_sha256=sha256(path),
            accepted_receipt=receipt_path.as_posix(), accepted_receipt_sha256=sha256(receipt_path),
            audit_sha256=sha256(audit_path), execution=read(attempt/"execution.json"), numerical=verified["numerical"]))
    return m, entries


def event_block(entry):
    task = entry["task"]
    require(sha256(entry["root"]) == entry["root_sha256"], "ROOT changed after acceptance")
    with uproot.open(entry["root"]) as root:
        e, l, s, f = (tree_arrays(root[name]) for name in
                     ("stack_event_v2", "stack_layer_v2", "stack_sensor_v2", "stack_photon_flow_v2"))
    e = {k:v[np.argsort(e["event_id"])] for k,v in e.items()}
    require(np.array_equal(e["event_id"], np.arange(task["events"])), "Missing/duplicate event IDs")
    n = task["events"]
    fields = ["detected", "generated_legacy", "births_tile", "detected_tile_birth", "zero_response",
              "births_nonoptical_parent", "births_optical_parent", "tile_nonoptical_edep_mev", "tile_optical_edep_mev"]
    collected = (f["birth_class"] == 1) & (f["fate"] == 0)
    tile_detected = np.bincount(f["event_id"].astype(int), weights=f["photon_count"]*collected, minlength=n)
    values = [e["detected_legacy"], e["generated_legacy"], e["births_tile"], tile_detected,
              (e["detected_legacy"] == 0).astype(int), *[e[k] for k in fields[5:]]]
    for layer in range(10):
        rows = np.flatnonzero(l["layer"] == layer)
        rows = rows[np.argsort(l["event_id"][rows])]
        require(np.array_equal(l["event_id"][rows], np.arange(n)), "Layer events differ")
        fields.append(f"layer_{layer}_detected")
        values.append(l["detected_all_origins"][rows])
    for sensor in task["config"]["active_sensor_copies"]:
        rows = np.flatnonzero(s["global_copy"] == sensor)
        rows = rows[np.argsort(s["event_id"][rows])]
        require(np.array_equal(s["event_id"][rows], np.arange(n)), "Sensor events differ")
        fields.append(f"sensor_{sensor}_detected")
        values.append(s["detected_all_origins"][rows])
    return dict(task_id=task["task_id"], block=task["block"], config=task["config"],
                values=np.column_stack(values).astype(float), fields=fields)
