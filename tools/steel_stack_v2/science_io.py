"""Frozen independent main registry and accepted OSC outputs."""
from __future__ import annotations

from collections import defaultdict
import hashlib
from pathlib import Path

from audit import audit_run, one
from benchmark_audit import numerical_trace
from model import canonical_json, configuration_hash, validate_tasks
from osc import read, require, sha256, scan_args
from precision_io import CONTROLLER_FILES as CALIBRATION_FILES, IDENTITIES, verify_controller
from precision_math import SPEC

CONTROLLER_FILES = (*CALIBRATION_FILES, "science.py", "science_io.py", "science_worker.py")
ALLOCATIONS = {(4,"back-center"):86800, (4,"edge-two"):13000,
               (24,"back-center"):16900, (24,"edge-two"):6900}


def validate_proposal(proposal, tasks, calibration_tasks, excluded):
    require(proposal["schema_version"] == "steel-gap-main-proposal-v1" and
            proposal["state"] == "frozen-proposal-not-submitted" and proposal["submitted"] is False and
            proposal["executable"] is True, "Not the executable frozen main proposal")
    require(proposal["calibration_included"] is False and proposal["benchmark_included"] is False and
            proposal["main_precision_verified"] is False, "Proposal mixes earlier samples or claims precision")
    require(proposal["resources"] == SPEC["resources"], "Formal resources differ from frozen proposal")
    summary = validate_tasks(tasks, total_event_budget=247200)
    require(summary == proposal["summary"] and summary["events"] == 247200 and len(tasks) == 2472,
            "Frozen formal sample size changed")
    used = set(excluded) | {t[k] for t in calibration_tasks for k in ("seed1","seed2")}
    groups = defaultdict(list)
    for task in tasks:
        require(task["stage"] == task["purpose"] == "science" and task["independent_sample"] is True and
                not task.get("repeat_of") and task["events"] == 100 and
                task["campaign_seed"] == SPEC["main_seed"], "Engineering/calibration/replay mixed into formal sample")
        require(task["seed1"] not in used and task["seed2"] not in used, "Formal seed overlaps earlier evidence")
        cfg = task["config"]
        require(configuration_hash(cfg) == task["configuration_hash"] and
                cfg["optical_numerics"] == {"profile":"painted-corner-v2","scale":16}, "Formal configuration changed")
        groups[(cfg["tile_thickness_mm"],cfg["layout"],cfg["gap_mm"])].append(task["block"])
    expected = {(t,l,g):n//100 for (t,l),n in ALLOCATIONS.items() for g in (.5,1.)}
    require(set(groups) == set(expected) and all(sorted(groups[k]) == list(range(v)) for k,v in expected.items()),
            "Formal configuration/block allocation changed")
    return summary


def read_campaign(root, campaign_hash, *, verify_bundles=True):
    root = Path(root).resolve()
    require(sha256(root/"campaign.json") == campaign_hash, "Formal campaign identity changed")
    campaign = read(root/"campaign.json")
    require(campaign["schema_version"] == "steel-gap-main-campaign-v1" and
            campaign["purpose"] == "science" and campaign["execution_authorized"] is True and
            Path(campaign["root"]).resolve() == root, "Wrong formal campaign or authorization")
    require(campaign["resources"] == SPEC["resources"] and
            1 <= campaign["array_capacity"] <= 1000, "Formal resource/scheduler policy changed")
    for name, expected in campaign["frozen_inputs"].items():
        p = root/name
        require(not p.is_symlink() and sha256(p) == expected, "Frozen formal input changed: "+name)
    control = campaign["control"]
    require(Path(control["root"]).resolve() == root/"controller", "Formal controller outside campaign")
    require(Path(control["stop_file"]).resolve() == root/"STOP.json", "Formal stop file outside campaign")
    verify_controller(control, files=CONTROLLER_FILES)
    require(read(root/"statistics-spec.json") == SPEC, "Formal statistical specification changed")
    tasks = read(root/"tasks.json")
    proposal = read(root/"proposal/manifest.json")
    summary = validate_proposal(proposal, tasks, read(root/"proposal/calibration-tasks.json"),
                                read(root/"proposal/excluded-benchmark-seeds.json"))
    require(summary == campaign["summary"] and read(root/"proposal/tasks.json") == tasks,
            "Execution registry differs from frozen proposal")
    require(campaign["simulation_identity"] == proposal["simulation_identity"], "Main simulation identity changed")
    ranges = campaign["bundles"]
    offset = 0
    for info in ranges:
        require(info["offset"] == offset and 0 < info["tasks"] <= campaign["array_capacity"], "Array ranges overlap or have gaps")
        require(Path(info["path"]).resolve() == root/"bundles"/info["name"], "Bundle outside formal campaign")
        offset += info["tasks"]
    require(offset == len(tasks), "Array ranges do not cover the formal registry")
    if verify_bundles:
        prep = read(root/"preparation.json")
        require(prep["campaign_sha256"] == campaign_hash, "Preparation belongs to another campaign")
        for name, expected in prep["artifacts"].items():
            require(not (root/name).is_symlink() and sha256(root/name) == expected, "Formal execution artifact changed: "+name)
        for info in ranges:
            check_bundle(campaign, tasks, info, campaign_hash)
    return campaign, tasks


def check_bundle(campaign, tasks, info, campaign_hash):
    bundle = Path(info["path"])
    m = read(bundle/"manifest.json")
    require(m["purpose"] == "science" and m["mock"] is False and
            m["optical_numerics"] == {"profile":"painted-corner-v2","scale":16}, "Not a real qualified formal bundle")
    require(m["task_offset"] == info["offset"] and m["science_control"]["campaign_sha256"] == campaign_hash,
            "Bundle range or campaign identity mismatch")
    require(all(m[k] == campaign["simulation_identity"][k] for k in IDENTITIES), "Bundle simulation identity mismatch")
    require(m["remote_output_root"] == str(Path(campaign["root"])/"results") and
            Path(m["remote_bundle_root"]).resolve() == bundle, "Bundle output/root mismatch")
    require(m["science_control"] == {**campaign["control"], "campaign_root":campaign["root"],
                                     "campaign_sha256":campaign_hash}, "Bundle controller/stop policy differs")
    registered = read(bundle/"tasks.json")
    expected = tasks[info["offset"]:info["offset"]+info["tasks"]]
    require(len(registered) == len(expected) and
            all({k:v for k,v in r.items() if k != "scan_args"} == t and r["scan_args"] == scan_args(t)
                for r,t in zip(registered,expected)), "Bundle changes frozen tasks, seeds, or macros")
    require(validate_tasks(registered) == m["summary"] and
            sha256(bundle/"tasks.json") == m["tasks_sha256"] and
            sha256(bundle/"array_task.py") == m["worker_sha256"], "Bundle registry/worker integrity failed")
    require(all(m["scheduler"][k] == v for k,v in
                dict(cpus_per_task=1,memory_gib=4,time_minutes=60,max_parallel=4,node_constraint="40core").items()),
            "Bundle scheduler resources changed")
    return m, registered


def audit_execution(campaign, m, task, index, receipt_path, manifest_hash):
    receipt_path = Path(receipt_path).resolve()
    e = read(receipt_path)
    require(e["status"] == "execution-complete-not-audited" and e["exit_code"] == 0, "Unfinished formal execution")
    require(e["task_id"] == task["task_id"] and e["events"] == task["events"] and
            e["array_index"] == index and e["global_task_index"] == m["task_offset"]+index and
            e["purpose"] == "science" and e["optical_numerics"] == task["config"]["optical_numerics"],
            "Formal execution task identity mismatch")
    require(e["manifest_sha256"] == manifest_hash and all(e[k] == m[k] for k in IDENTITIES),
            "Formal execution environment identity mismatch")
    expected = Path(m["remote_output_root"])/task["task_id"]/("job-"+e["scheduler_job"]+"-task-"+str(index))
    require(receipt_path.parent == expected.resolve(), "Formal result uses another array/attempt path")
    run = Path(e["run_dir"]).resolve()
    require(receipt_path.parent in run.parents, "Formal run outside task attempt")
    actual = {}
    for p in run.rglob("*"):
        require(not p.is_symlink(), "Symlink in formal output")
        if p.is_file(): actual[p.relative_to(run).as_posix()] = sha256(p)
    require(actual and actual == e["artifacts"], "Formal output inventory/checksum changed")
    checks = audit_run(run, task["config"], task["events"], expected_seeds=(task["seed1"],task["seed2"]))
    numeric = numerical_trace(one((run/"outputs").glob("*.optical-numerics.jsonl"),"formal numerical trace"),
                              task["config"],task["events"])
    require(checks["passed"] and numeric["passed"], "Formal ledger/numerical audit failed")
    return dict(passed=True,purpose="science",accepted_statistical_evidence=True,
        task_id=task["task_id"],events=task["events"],configuration_hash=task["configuration_hash"],
        campaign_sha256=m["science_control"]["campaign_sha256"],manifest_sha256=manifest_hash,
        execution_sha256=sha256(receipt_path),execution=str(receipt_path),run_dir=str(run),
        controller_sha256=campaign["control"]["sha256"],audit=checks,numerical=numeric)


def receipt_entry(campaign, info, task, index, job, *, revalidate=False):
    m = read(Path(info["path"])/"manifest.json")
    manifest_hash = sha256(Path(info["path"])/"manifest.json")
    directory = Path(campaign["root"])/"results"/task["task_id"]
    attempts = list(directory.glob("job-*"))
    require(len(attempts) == 1, "Missing/ambiguous formal attempt: "+task["task_id"])
    attempt = attempts[0]
    require(attempt.name == "job-"+job+"-task-"+str(index) and not(attempt/"failure.json").exists(),
            "Failed/wrong formal attempt")
    accepted = read(attempt/"accepted.json")
    require(accepted["accepted_for_main"] is True and accepted["accepted_for_calibration"] is False and
            accepted["task_id"] == task["task_id"] and accepted["manifest_sha256"] == manifest_hash and
            accepted["registered_task_sha256"] == hashlib.sha256(canonical_json(task)).hexdigest(),
            "Wrong main acceptance receipt or mixed sample")
    audit_path = attempt/"science-audit.json"
    require(sha256(audit_path) == accepted["audit_sha256"] and
            sha256(attempt/"execution.json") == accepted["execution_sha256"], "Main accepted receipt checksum mismatch")
    audit = read(audit_path)
    require(audit["passed"] and audit["purpose"] == "science" and audit["accepted_statistical_evidence"] is True and
            audit["controller_sha256"] == campaign["control"]["sha256"] and
            audit["campaign_sha256"] == accepted["campaign_sha256"] == m["science_control"]["campaign_sha256"],
            "Wrong formal audit identity")
    if revalidate:
        require(audit_execution(campaign,m,task,index,attempt/"execution.json",manifest_hash) == audit,
                "Revalidated formal audit differs")
    root = one((Path(audit["run_dir"])/"outputs").glob("*.root"),"formal ROOT")
    return dict(task=task,root=str(root),root_sha256=sha256(root),execution=read(attempt/"execution.json"),
        accepted_receipt=str(attempt/"accepted.json"),accepted_receipt_sha256=sha256(attempt/"accepted.json"),
        audit_sha256=sha256(audit_path),numerical=audit["numerical"])


def scheduler_rows(text):
    import csv, io
    rows = [{k.strip():v.strip() for k,v in r.items() if k is not None}
            for r in csv.DictReader(io.StringIO(text),delimiter="|")]
    require(rows and {"JobID","State","ExitCode","AllocCPUS"} <= set(rows[0]), "Malformed formal scheduler accounting")
    require(len({r["JobID"] for r in rows}) == len(rows), "Duplicate scheduler task rows")
    return {r["JobID"]:r for r in rows}


def require_completed(rows, job, count):
    expected = {job+"_"+str(i) for i in range(1,count+1)}
    actual = {k for k in rows if k.startswith(job+"_")}
    require(actual == expected, "Missing/unregistered final scheduler task")
    require(all(rows[k]["State"] == "COMPLETED" and rows[k]["ExitCode"] == "0:0" and
                rows[k]["AllocCPUS"] == "1" for k in expected), "Formal task did not complete with one CPU and exit zero")
