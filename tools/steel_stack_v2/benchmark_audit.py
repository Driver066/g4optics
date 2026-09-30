#!/usr/bin/env python3
"""Audit benchmark output and resource use without drawing gap/layout conclusions."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

from audit import audit_run, one
from model import validate_tasks
from osc import sha256, require

PURPOSE = "engineering-benchmark-not-scientific-evidence"


def numerical_trace(path, config, events):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    require(rows and rows[0]["kind"] == "run" and rows[-1]["kind"] == "run_end",
            "Incomplete numerical trace")
    header = rows[0]
    identity = header["identity"]
    require(config["optical_numerics"] == {"profile": "painted-corner-v2", "scale": 16},
            "Benchmark requires the qualified v2/16 numerical baseline")
    require(all(identity.get(k) == v for k, v in config["optical_numerics"].items()) and
            identity["diagnostic_primary"] is False, "Numerical trace identity mismatch")
    tolerance = identity["surface_tolerance_mm"]
    require(tolerance == 1.e-9 and header["boundary_class"] == "PaintedCornerBoundary",
            "Numerical boundary/tolerance changed")
    require(header["post_step_order"] == [[p, True] for p in
            ("Transportation", "OpAbsorption", "OpBoundary", "OpWLS2", "Scintillation")],
            "Optical process order/activation changed")
    ends = {}
    counts = Counter()
    seen = set()
    reflections = set()
    terminals = set()
    for row in rows[1:-1]:
        event = row["event"]
        require(type(event) is int and 0 <= event < events, "Numerical event ID out of range")
        require(event not in ends, "Numerical record after event end")
        if row["kind"] == "event_end":
            require(all(row[k] == 0 for k in
                    ("boundary_no_rindex", "painted_zero_step_escapes", "unsettled")),
                    "Numerical transport hard failure")
            require(row["corrections"] == counts[event], "Numerical correction count mismatch")
            ends[event] = row
            continue
        # Production keeps a sparse boundary/terminal tail for affected tracks.
        # These are physical records, separate from the position correction.
        if row["kind"] in ("boundary", "terminal"):
            require(row["status"] != 14, "Raw NoRINDEX in numerical trace")
            if row["kind"] == "boundary":
                require(row["painted_zero_step_escape"] is False, "Raw painted zero-step escape")
                if row["edge_reflection"] and row["status"] == 7:
                    reflections.add((event, row["track"], row["step"]))
            else:
                key = (event, row["track"])
                require(key not in terminals, "Repeated numerical terminal")
                require(row["sensor"] == -1 or row["sensor"] in config["active_sensor_copies"],
                        "Invalid numerical sensor")
                terminals.add(key)
            continue
        require(row["kind"] == "correction", "Unexpected production numerical record")
        key = (event, row["track"], row["reflection_step"])
        require(key not in seen, "Repeated numerical correction")
        seen.add(key)
        counts[event] += 1
        require(row["raw_status"] == 7 and row["step"] == row["reflection_step"] and
                row["faces"] in (3, 5, 6, 7) and row["scale"] == 16 and
                row["tolerance_mm"] == tolerance, "Correction outside qualified scope")
        require(type(row["tile"]) is int and 0 <= row["tile"] < 10 and
                row["pre"] == row["post"] == row["next"] == ["Tank", row["tile"]] and
                "raw_post" in row, "Correction volume state mismatch")
        require(all(row[k] is True for k in ("same_volume_navigator_verified",
                "transport_cache_verified", "physical_proposals_delegated_unchanged")) and
                row["particle_change_modified_fields"] == ["position", "geometry_state"],
                "Correction changed physical proposals or lacks transport verification")
        before, after = row["before_mm"], row["after_mm"]
        require(len(before) == len(after) == 3 and all(math.isfinite(x) for x in before+after),
                "Invalid correction coordinates")
        distance = math.dist(before, after)
        require(0 < distance <= math.sqrt(3)*17*tolerance and
                math.isclose(distance, row["displacement_mm"], rel_tol=1.e-12, abs_tol=1.e-20),
                "Correction displacement exceeds bound or differs")
    require(set(ends) == set(range(events)), "Missing numerical event end")
    require(seen <= reflections and all((event, track) in terminals for event, track, _ in seen),
            "Correction lacks its physical reflection or terminal record")
    return {"passed": True, "events": events, "corrections": len(seen),
            "boundary_no_rindex": 0, "painted_zero_step_escapes": 0, "unsettled": 0,
            "sha256": sha256(path)}


def collect(bundle, output_root, expected_manifest_sha256):
    manifest_path = bundle/"manifest.json"
    require(sha256(manifest_path) == expected_manifest_sha256, "Submitted manifest changed")
    manifest = json.loads(manifest_path.read_text())
    require(manifest["purpose"] == "benchmark" and manifest["mock"] is False,
            "Only real benchmark outputs belong in this resource report")
    require(sha256(bundle/"tasks.json") == manifest["tasks_sha256"], "Task registry changed")
    tasks = json.loads((bundle/"tasks.json").read_text())
    require(validate_tasks(tasks, total_event_budget=manifest["total_event_budget"]) == manifest["summary"],
            "Benchmark registry summary mismatch")
    require(output_root.resolve() == Path(manifest["remote_output_root"]).resolve(),
            "Benchmark output root differs from submitted plan")
    rows = []
    for index, task in enumerate(tasks, 1):
        require(task["stage"] == "benchmark" and task["purpose"] == PURPOSE and not task.get("repeat_of"),
                "Scientific/benchmark or repeated sample mix")
        receipts = list((output_root/task["task_id"]).glob("job-*/execution.json"))
        require(len(receipts) == 1, "Missing or ambiguous task attempt: "+task["task_id"])
        path = receipts[0]
        execution = json.loads(path.read_text())
        require(execution["status"] == "execution-complete-not-audited" and execution["exit_code"] == 0,
                "Incomplete benchmark task")
        require(execution["task_id"] == task["task_id"] and execution["events"] == task["events"] and
                execution["array_index"] == index and execution["purpose"] == PURPOSE and
                execution["optical_numerics"] == task["config"]["optical_numerics"],
                "Execution task identity mismatch")
        require(execution["manifest_sha256"] == expected_manifest_sha256 and all(
                execution[key] == manifest[key] for key in
                ("executable_sha256", "source_manifest_sha256", "sif_sha256", "dataset_receipt_sha256")),
                "Execution environment identity mismatch")
        run_dir = Path(execution["run_dir"]).resolve()
        require(path.parent.resolve() in run_dir.parents, "Run directory escapes task attempt")
        actual = {}
        for artifact in run_dir.rglob("*"):
            require(not artifact.is_symlink(), "Symlink in benchmark output")
            if artifact.is_file():
                actual[artifact.relative_to(run_dir).as_posix()] = sha256(artifact)
        require(actual and actual == execution["artifacts"], "Output inventory/checksum changed")
        checks = audit_run(run_dir, task["config"], task["events"],
                           expected_seeds=(task["seed1"], task["seed2"]))
        require(checks["passed"] is True, "Numerical/ledger benchmark audit failed")
        numerical = numerical_trace(one((run_dir/"outputs").glob("*.optical-numerics.jsonl"),
                                       "numerical trace"), task["config"], task["events"])
        seconds, rss = execution["launcher_elapsed_seconds"], execution["child_max_rss_kib"]
        cpu = execution["child_user_seconds"] + execution["child_system_seconds"]
        require(all(math.isfinite(v) and v > 0 for v in (seconds, rss, cpu)),
                "Missing valid resource measurements")
        rows.append(dict(task_id=task["task_id"], config=task["config"], events=task["events"],
            block=task["block"], hostname=execution["hostname"], scheduler_job=execution["scheduler_job"],
            elapsed_seconds=seconds, seconds_per_event=seconds/task["events"],
            child_max_rss_mib=rss/1024, child_cpu_seconds=cpu,
            audit=checks, numerical=numerical, execution_sha256=sha256(path)))
    worst = max(r["seconds_per_event"] for r in rows)
    return dict(passed=True, purpose="engineering-resource-benchmark-not-scientific-gap-evidence",
        tasks=len(rows), events=sum(r["events"] for r in rows), rows=rows,
        resource_summary={"slowest_observed_seconds_per_event": worst,
            "peak_child_rss_mib": max(r["child_max_rss_mib"] for r in rows),
            "aggregate_launcher_hours": sum(r["elapsed_seconds"] for r in rows)/3600,
            "one_hour_event_ceiling_with_2x_observed_time_margin": max(0, int(3300/(2*worst)))},
        caveats=["Short-run measurements include process startup and may miss rare costly neutron showers.",
                 "Per-job event ceiling is a resource recommendation, not a scientific statistical budget.",
                 "Benchmark events are excluded from scientific gap/layout comparisons.",
                 "Scheduler completion and allocation accounting must also be inspected."],
        gap_selected=None, scientific_equivalence_claimed=False,
        manifest_sha256=expected_manifest_sha256, auditor_sha256=sha256(Path(__file__)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    require(not args.report.exists(), "Keep each audit result; select a fresh report path")
    try:
        result = collect(args.bundle.resolve(), args.output_root.resolve(), args.manifest_sha256)
    except (ValueError, KeyError, OSError, TypeError) as error:
        result = dict(passed=False, error=str(error), manifest_sha256=args.manifest_sha256)
    with args.report.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
