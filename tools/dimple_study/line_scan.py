#!/usr/bin/env python3
"""Frozen local 5,200-event dimple line scan; serial tasks, no automatic retry."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from optical_smoke import macro

ROOT = Path(__file__).resolve().parents[2]
SIMULATION_COMMIT = "5d6c50d37e33178e27710df5cc13e0127593acbe"
POSITIONS = (-40, -10, -5, -3.2, -2.8, -1.5, 0, 1.5, 2.8, 3.2, 5, 10, 40)
IMAGE = "sha256:316f3970072d236f994a98f05b9702f7888c577c0b1971ce6e826689220b71ee"
TOOLS = ("tools/dimple_study/line_scan.py", "tools/dimple_study/optical_smoke.py",
         "tools/dimple_study/optical_audit.py", "tools/layout_study/audit.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def tasks() -> list[dict]:
    result = []
    for block in range(2):
        for index, x in enumerate(POSITIONS):
            for geometry in ("flat", "dimple"):
                i = len(result)
                result.append({"name": f"b{block}-p{index:02d}-{geometry}", "block": block,
                               "geometry": geometry, "x_mm": x, "events": 100,
                               "variant": "on", "diagnostics": True, "require_response": False,
                               "seeds": [1910081001 + 2*i, 1910081002 + 2*i]})
    return result


def check_plan(manifest: dict) -> None:
    cases = manifest["tasks"]
    expected = {(g, x, b) for g in ("flat", "dimple") for x in POSITIONS for b in range(2)}
    actual = {(c["geometry"], c["x_mm"], c["block"]) for c in cases}
    if len(cases) != 52 or actual != expected or len({c["name"] for c in cases}) != 52:
        raise ValueError("Line scan must contain exactly 52 distinct registered tasks")
    seeds = [s for c in cases for s in c["seeds"]]
    if (len(seeds) != 104 or len(set(seeds)) != 104 or
        any(not isinstance(s, int) or not 0 < s < 2147483647 for s in seeds) or
        set(seeds) & set(manifest["excluded_engineering_seed_values"])):
        raise ValueError("Seeds must be unique, valid and independent of engineering seeds")
    if any(c["events"] != 100 or c["diagnostics"] is not True or
           c["require_response"] is not False or c["variant"] != "on" for c in cases):
        raise ValueError("Unexpected event budget or diagnostic policy")
    if sum(c["events"] for c in cases) != 5200 or manifest["event_budget"] != 5200:
        raise ValueError("Event budget differs from the fixed 5,200 events")


def prepare(batch: Path, accepted: Path) -> None:
    if git("status", "--porcelain").strip():
        raise ValueError("Commit the scan tools before freezing a campaign")
    validation = json.loads(git("show", f"{SIMULATION_COMMIT}:tools/dimple_study/optical-validation.json"))
    original_manifest = json.loads((accepted / "manifest.json").read_text())
    verification = json.loads((accepted / "verification.json").read_text())
    executable = accepted / "build/OpNovice2"
    if (verification["status"] != "passed" or
        sha(accepted / "verification.json") != validation["verification_sha256"] or
        sha(accepted / "manifest.json") != validation["manifest_sha256"] or
        sha(accepted / "build-receipt.json") != validation["build_receipt_sha256"] or
        sha(executable) != validation["candidate_executable_sha256"]):
        raise ValueError("Accepted optical source/binary evidence does not match")
    for relative, expected in validation["source_sha256"].items():
        for ref in (SIMULATION_COMMIT, "HEAD"):
            if hashlib.sha256(git("show", f"{ref}:{relative}")).hexdigest() != expected:
                raise ValueError(f"Simulation source changed: {relative}")
    excluded = sorted({s for c in original_manifest["cases"] for s in c["seeds"]}
                      | set(range(1907100001, 1907100025)))
    template = (accepted / "optical-template.mac").read_bytes()
    if hashlib.sha256(template).hexdigest() != original_manifest["template"]["sha256"]:
        raise ValueError("Accepted optical template changed")
    batch.mkdir(parents=True, exist_ok=False)
    (batch / "runtime").mkdir()
    shutil.copy2(executable, batch / "runtime/OpNovice2")
    (batch / "runtime/OpNovice2").chmod(0o555)
    tool_hashes = {}
    for relative in TOOLS:
        content = git("show", f"HEAD:{relative}")
        target = batch / "runtime" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        tool_hashes[relative] = sha(target)
    for relative in validation["source_sha256"]:
        target = batch / "simulation-source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git("show", f"{SIMULATION_COMMIT}:{relative}"))
    (batch / "optical-template.mac").write_bytes(template)
    shutil.copy2(accepted / "build-receipt.json", batch / "accepted-build-receipt.json")
    cases = tasks()
    for case in cases:
        directory = batch / "tasks" / case["name"]
        directory.mkdir(parents=True)
        (directory / "run.mac").write_text(macro(template.decode(), case))
        case["macro_sha256"] = sha(directory / "run.mac")
    manifest = {"schema": "dimple-local-line-v1", "created_utc": utc(),
                "simulation_commit": SIMULATION_COMMIT,
                "tool_commit": git("rev-parse", "HEAD").decode().strip(),
                "source_sha256": validation["source_sha256"], "tool_sha256": tool_hashes,
                "executable_sha256": sha(batch / "runtime/OpNovice2"), "image_id": IMAGE,
                "dataset_manifest_sha256": validation["dataset_manifest_sha256"],
                "accepted_optical_verification_sha256": validation["verification_sha256"],
                "accepted_build_receipt_sha256": sha(batch / "accepted-build-receipt.json"),
                "template_sha256": sha(batch / "optical-template.mac"),
                "excluded_engineering_seed_values": excluded,
                "x_mm": list(POSITIONS), "y_mm": 0, "source_z_mm": 4,
                "primary": "one 1 MeV electron per event along -Z",
                "event_budget": 5200, "task_timeout_seconds": 600,
                "execution": "Serial; one task at a time; no automatic retry or extension",
                "primary_endpoint": "mean collected photons D per incident electron, including zeros",
                "secondary_endpoints": ["mean legacy G", "sum(D)/sum(G)", "optical-parent births"],
                "contrast": "dimple minus flat at each x; independent samples, no seed pairing",
                "scope": "exploratory y=0 line; not a two-dimensional uniformity or material validation",
                "tasks": cases}
    check_plan(manifest)
    save(batch / "manifest.json", manifest)
    manifest_sha = sha(batch / "manifest.json")
    (batch / "manifest.sha256").write_text(manifest_sha + "\n")
    save(batch / "progress.json", {"state": "prepared", "accepted_tasks": 0,
                                    "accepted_events": 0, "total_tasks": 52, "event_budget": 5200,
                                    "manifest_sha256": manifest_sha, "updated_utc": utc()})
    print(f"Prepared 52 tasks / 5,200 events. Manifest SHA256: {manifest_sha}")


def verify_inputs(batch: Path, manifest: dict, expected_manifest: str) -> None:
    if sha(batch / "manifest.json") != expected_manifest:
        raise ValueError("Frozen manifest changed")
    check_plan(manifest)
    if sha(batch / "runtime/OpNovice2") != manifest["executable_sha256"]:
        raise ValueError("Frozen executable changed")
    for relative, expected in manifest["tool_sha256"].items():
        if sha(batch / "runtime" / relative) != expected:
            raise ValueError(f"Frozen runtime tool changed: {relative}")
    for relative, expected in manifest["source_sha256"].items():
        if sha(batch / "simulation-source" / relative) != expected:
            raise ValueError(f"Recorded simulation source changed: {relative}")
    for case in manifest["tasks"]:
        if sha(batch / "tasks" / case["name"] / "run.mac") != case["macro_sha256"]:
            raise ValueError(f"Frozen macro changed: {case['name']}")


def summarize(batch: Path, manifest: dict, reports: list[dict]) -> None:
    rows = []
    for x in POSITIONS:
        for geometry in ("flat", "dimple"):
            matched = [r for r in reports if r["geometry"] == geometry and r["x_mm"] == x]
            if len(matched) != 2 or sum(r["events"] for r in matched) != 200:
                raise ValueError("Incomplete point; refusing a complete line summary")
            g = sum(r["generated_photons"] for r in matched)
            d = sum(r["detected_photons"] for r in matched)
            rows.append({"geometry": geometry, "x_mm": x, "events": 200,
                         "mean_D": d / 200, "mean_G": g / 200,
                         "legacy_ratio_of_totals_D_over_G": d / g if g else None,
                         "optical_parent_births": sum(r["diagnostics"]["optical_parent_scintillation_births"] for r in matched),
                         "block_0_mean_D": matched[0]["detected_photons"] / 100,
                         "block_1_mean_D": matched[1]["detected_photons"] / 100})
    with (batch / "line-summary.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save(batch / "verification.json", {"status": "passed", "completed_utc": utc(),
         "manifest_sha256": sha(batch / "manifest.json"), "accepted_tasks": 52,
         "accepted_events": 5200, "scope": manifest["scope"], "reports": reports,
         "line_summary_sha256": sha(batch / "line-summary.csv")})


def run(batch: Path, expected_manifest: str) -> None:
    from optical_audit import audit_case
    manifest = json.loads((batch / "manifest.json").read_text())
    verify_inputs(batch, manifest, expected_manifest)
    if os.environ.get("G4RUN_MANAGER_TYPE") != "Serial":
        raise ValueError("Serial run manager required")
    if subprocess.check_output(["geant4-config", "--version"], text=True).strip() != "11.4.2":
        raise ValueError("Geant4 11.4.2 required")
    for case in manifest["tasks"]:
        directory = batch / "tasks" / case["name"]
        if any((directory / name).exists() for name in ("receipt.json", "simulation.log", "result.root")):
            raise ValueError("Existing attempts are preserved; automatic reruns are forbidden")
    with (batch / "execution-lock.json").open("x") as lock:
        json.dump({"pid": os.getpid(), "started_utc": utc(), "manifest_sha256": expected_manifest}, lock)
    reports = []
    current = None
    try:
        dataset_manifest = Path("/g4data/layout-study-SHA256SUMS")
        if sha(dataset_manifest) != manifest["dataset_manifest_sha256"]:
            raise ValueError("Geant4 data receipt differs from accepted optical check")
        subprocess.run(["sha256sum", "--check", "--status", str(dataset_manifest)], check=True)
        save(batch / "data-preflight.json", {"status": "passed", "checked_utc": utc(),
                                            "dataset_manifest_sha256": sha(dataset_manifest)})
        for case in manifest["tasks"]:
            current = case["name"]
            verify_inputs(batch, manifest, expected_manifest)
            directory = batch / "tasks" / current
            save(batch / "progress.json", {"state": "running", "current_task": current,
                 "accepted_tasks": len(reports), "accepted_events": 100*len(reports),
                 "total_tasks": 52, "event_budget": 5200, "updated_utc": utc()})
            started = time.monotonic()
            with (directory / "simulation.log").open("wb") as output:
                try:
                    result = subprocess.run([str(batch / "runtime/OpNovice2"), "run.mac"],
                        cwd=directory, stdout=output, stderr=subprocess.STDOUT,
                        timeout=manifest["task_timeout_seconds"])
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = None
            receipt = {"task": current, "exit_code": code, "ended_utc": utc(),
                       "elapsed_seconds": time.monotonic() - started,
                       "executable_sha256": sha(batch / "runtime/OpNovice2"),
                       "macro_sha256": sha(directory / "run.mac"),
                       "log_sha256": sha(directory / "simulation.log")}
            save(directory / "receipt.json", receipt)
            verify_inputs(batch, manifest, expected_manifest)
            report = audit_case(directory, case, code)
            report.update(task=current, block=case["block"])
            save(directory / "audit.json", report)
            reports.append(report)
            print(f"Accepted {len(reports)}/52: {current} (100 events)", flush=True)
        summarize(batch, manifest, reports)
        save(batch / "progress.json", {"state": "completed", "accepted_tasks": 52,
             "accepted_events": 5200, "total_tasks": 52, "event_budget": 5200, "updated_utc": utc()})
        print("Completed and accepted all 5,200 events.", flush=True)
    except Exception as error:
        stopped = {"state": "stopped", "current_task": current,
                   "accepted_tasks": len(reports), "accepted_events": 100*len(reports),
                   "reason": str(error), "automatic_retry": False, "updated_utc": utc()}
        save(batch / "STOP.json", stopped)
        save(batch / "progress.json", stopped)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--accepted", type=Path)
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.accepted:
            parser.error("prepare requires --accepted optical-v1")
        prepare(args.batch.resolve(), args.accepted.resolve())
    else:
        if not args.manifest_sha256:
            parser.error("run requires the previously recorded --manifest-sha256")
        run(args.batch.resolve(), args.manifest_sha256)


if __name__ == "__main__":
    main()
