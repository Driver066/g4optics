#!/usr/bin/env python3
"""Rebuild and reproduce the published dimple campaign from a fresh source tree.

No Git metadata, historical output directory, accepted binary, or private image
is needed. Every attempt uses a new directory; failed attempts are not retried.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

from optical_smoke import macro


ROOT = Path(__file__).resolve().parents[2]
PUBLISHED = "tools/dimple_study/results/line-v1/campaign-manifest.json"
TEMPLATE = "test/OpNovice2/ej200_sipm_gps_test.mac"
ENTRY = "tools/dimple_study/reproduce_line.py"
TOOLS = (ENTRY, "tools/dimple_study/optical_smoke.py",
         "tools/dimple_study/optical_audit.py", "tools/layout_study/audit.py")
POSITIONS = (-40, -10, -5, -3.2, -2.8, -1.5, 0, 1.5, 2.8, 3.2, 5, 10, 40)
DATA_RECEIPT = Path("/g4data/layout-study-SHA256SUMS")


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def published_plan(published: dict) -> list[dict]:
    cases = published["tasks"]
    expected = {(g, x, b) for g in ("flat", "dimple") for x in POSITIONS for b in range(2)}
    require(len(cases) == 52 and
            {(c["geometry"], c["x_mm"], c["block"]) for c in cases} == expected and
            len({c["name"] for c in cases}) == 52, "Published campaign design is not the 52-task line scan")
    seeds = [seed for case in cases for seed in case["seeds"]]
    require(len(seeds) == len(set(seeds)) == 104 and
            all(type(seed) is int and 0 < seed < 2147483647 for seed in seeds),
            "Published seeds must be 104 distinct valid integers")
    require(not set(seeds).intersection(published["excluded_engineering_seed_values"]),
            "Published line seeds overlap excluded engineering seeds")
    require(all(case["events"] == 100 and case["variant"] == "on" and
                case["diagnostics"] is True and case["require_response"] is False
                and len(case["seeds"]) == 2 and case["name"] ==
                f"b{case['block']}-p{POSITIONS.index(case['x_mm']):02d}-{case['geometry']}"
                for case in cases) and
            published["event_budget"] == 5200, "Published event budget or diagnostic policy changed")
    return cases


def selected_tasks(published: dict, mode: str) -> list[dict]:
    require(mode in ("smoke", "full"), "Unknown reproduction mode")
    cases = published_plan(published)
    selected = cases if mode == "full" else [case for case in cases if case["name"] in
                                             ("b0-p06-flat", "b0-p06-dimple")]
    require(mode == "full" or (len(selected) == 2 and all(case["x_mm"] == 0 and
            case["block"] == 0 for case in selected)), "Missing published centre smoke tasks")
    result = []
    for original in selected:
        case = {key: value for key, value in original.items() if key != "macro_sha256"}
        case["published_macro_sha256"] = original["macro_sha256"]
        if mode == "smoke":
            case["events"] = 5
        result.append(case)
    return result


def verify_files(base: Path, hashes: dict) -> None:
    for relative, expected in hashes.items():
        path = Path(relative)
        require(not path.is_absolute() and ".." not in path.parts, "Invalid frozen relative path")
        require(sha(base / relative) == expected, f"Frozen input changed: {relative}")


def source_files(base: Path) -> set[str]:
    project = base / "test/OpNovice2"
    return {str(path.relative_to(base)) for path in
            [project / "CMakeLists.txt", project / "OpNovice2.cc",
             *project.glob("src/*.cc"), *project.glob("include/*.hh")]}


def prepare(output: Path, mode: str, image_metadata: Path | None = None) -> None:
    require(not output.exists(), "Output already exists; choose a new attempt directory")
    published_path = ROOT / PUBLISHED
    published = json.loads(published_path.read_text())
    cases = selected_tasks(published, mode)
    verify_files(ROOT, published["source_sha256"])
    project = ROOT / "test/OpNovice2"
    require(source_files(ROOT) == set(published["source_sha256"]),
            "Current C++/headers/CMake file set differs from the published source")
    template = (ROOT / TEMPLATE).read_bytes()
    require(hashlib.sha256(template).hexdigest() == published["template_sha256"],
            "Current optical template differs from the published template")
    for relative in TOOLS[1:]:
        require(sha(ROOT / relative) == published["tool_sha256"][relative],
                f"Historical macro/audit tool differs from the published campaign: {relative}")
    metadata_bytes = image_metadata.read_bytes() if image_metadata else None
    if metadata_bytes is not None:
        json.loads(metadata_bytes)
    # All read-only checks precede creation. A partial failed preparation is
    # preserved and must be replaced by a different output directory.
    output.mkdir(parents=True, exist_ok=False)
    (output / "published-campaign-manifest.json").write_bytes(published_path.read_bytes())
    source_hashes = {}
    paths = sorted({ROOT / relative for relative in published["source_sha256"]} |
                   set(project.glob("*.mac")) | set(project.glob("*.out")))
    for path in paths:
        relative = str(path.relative_to(ROOT))
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        source_hashes[relative] = sha(target)
    verify_files(output / "source", published["source_sha256"])
    tool_hashes = {}
    for relative in TOOLS:
        target = output / "runtime" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        tool_hashes[relative] = sha(target)
    for case in cases:
        directory = output / "tasks" / case["name"]
        directory.mkdir(parents=True)
        (directory / "run.mac").write_text(macro(template.decode(), case))
        case["macro_sha256"] = sha(directory / "run.mac")
        if mode == "full":
            require(case["macro_sha256"] == case["published_macro_sha256"],
                    f"Regenerated macro differs from publication: {case['name']}")
    metadata = None
    if metadata_bytes is not None:
        (output / "image-metadata.json").write_bytes(metadata_bytes)
        metadata = {"file": "image-metadata.json", "sha256": sha(output / "image-metadata.json"),
                    "provenance": "host supplied; this tool does not infer the running image identity"}
    manifest = {
        "schema": "dimple-source-reproduction-v1", "mode": mode, "created_utc": utc(),
        "published_manifest_sha256": sha(output / "published-campaign-manifest.json"),
        "published_simulation_commit": published["simulation_commit"],
        "published_executable_sha256": published["executable_sha256"],
        "source_sha256": source_hashes, "tool_sha256": tool_hashes,
        "template_sha256": published["template_sha256"], "image_metadata": metadata,
        "dataset_manifest_sha256": published["dataset_manifest_sha256"],
        "event_budget": sum(case["events"] for case in cases), "task_timeout_seconds": 600,
        "tasks": cases,
        "scope": ("10 engineering events reusing published centre seeds; not the original 5200-event campaign"
                  if mode == "smoke" else "Reexecution of the published 52 tasks / 5200 events"),
        "reproducibility_boundary": "Newly compiled executable; cross-platform or cross-architecture bitwise equality is not promised",
    }
    save(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha(output / "manifest.json") + "\n")
    save(output / "progress.json", {"state": "prepared", "updated_utc": utc(),
         "mode": mode, "event_budget": manifest["event_budget"], "accepted_events": 0})
    print(f"Prepared {mode}: {len(cases)} tasks / {manifest['event_budget']} events", flush=True)
    print(f"Manifest SHA256: {sha(output / 'manifest.json')}", flush=True)


def verify_snapshot(output: Path, expected_manifest: str | None = None) -> dict:
    expected = expected_manifest or (output / "manifest.sha256").read_text().strip()
    require(sha(output / "manifest.json") == expected, "Frozen reproduction manifest changed")
    manifest = json.loads((output / "manifest.json").read_text())
    require(Path(__file__).resolve() == (output / "runtime" / ENTRY).resolve(),
            "Build/run the frozen runtime/tools/dimple_study/reproduce_line.py from this output directory")
    require(sha(output / "published-campaign-manifest.json") == manifest["published_manifest_sha256"],
            "Published campaign manifest copy changed")
    published = json.loads((output / "published-campaign-manifest.json").read_text())
    planned = selected_tasks(published, manifest["mode"])
    require([{k: v for k, v in case.items() if k != "macro_sha256"} for case in manifest["tasks"]] == planned,
            "Frozen reproduction task design changed")
    require(manifest["event_budget"] == sum(case["events"] for case in planned), "Event budget changed")
    verify_files(output / "source", manifest["source_sha256"])
    require(source_files(output / "source") == set(published["source_sha256"]),
            "Frozen C++/headers/CMake file set changed")
    verify_files(output / "runtime", manifest["tool_sha256"])
    require(sha(output / "source" / TEMPLATE) == manifest["template_sha256"], "Frozen template changed")
    for case in manifest["tasks"]:
        require(sha(output / "tasks" / case["name"] / "run.mac") == case["macro_sha256"],
                f"Frozen macro changed: {case['name']}")
    if manifest["image_metadata"]:
        metadata = manifest["image_metadata"]
        require(sha(output / metadata["file"]) == metadata["sha256"], "Host image metadata changed")
    return manifest


def runtime_environment() -> dict:
    require(os.environ.get("G4RUN_MANAGER_TYPE") == "Serial", "Set G4RUN_MANAGER_TYPE=Serial")
    version = subprocess.check_output(["geant4-config", "--version"], text=True).strip()
    require(version == "11.4.2", "Geant4 11.4.2 is required")
    packages = {}
    for package in ("numpy", "uproot"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {"geant4_version": version, "architecture": platform.machine(),
            "platform": platform.platform(), "python_version": platform.python_version(),
            "python_executable": sys.executable, "python_packages": packages,
            "run_manager": "Serial"}


def stopped(output: Path, phase: str, error: BaseException, **details) -> None:
    record = {"state": "stopped", "phase": phase, "reason": str(error),
              "automatic_retry": False, "updated_utc": utc(), **details}
    save(output / "STOP.json", record)
    save(output / "progress.json", record)


def build(output: Path, expected_manifest: str | None = None) -> None:
    manifest = verify_snapshot(output, expected_manifest)
    require(not any((output / name).exists() for name in
                    ("build", "build.log", "build-attempt.json", "build-receipt.json", "STOP.json")),
            "Existing build attempt preserved; prepare a new output directory")
    with (output / "build-attempt.json").open("x") as handle:
        json.dump({"started_utc": utc(), "manifest_sha256": sha(output / "manifest.json")}, handle)
    try:
        environment = runtime_environment()
        commands = [["cmake", "-S", str(output / "source/test/OpNovice2"), "-B", str(output / "build"),
                     "-DCMAKE_BUILD_TYPE=Release", "-DWITH_GEANT4_UIVIS=OFF"],
                    ["cmake", "--build", str(output / "build"), "--parallel", "2"]]
        save(output / "progress.json", {"state": "building", "updated_utc": utc()})
        with (output / "build.log").open("xb") as log:
            for command in commands:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
                require(result.returncode == 0, f"Build command exited {result.returncode}: {command}")
        verify_snapshot(output, expected_manifest)
        receipt = {"status": "passed", "finished_utc": utc(), "commands": commands,
                   "manifest_sha256": sha(output / "manifest.json"), "environment": environment,
                   "image_metadata": manifest["image_metadata"], "source_sha256": manifest["source_sha256"],
                   "executable_sha256": sha(output / "build/OpNovice2"),
                   "build_log_sha256": sha(output / "build.log"),
                   "scope": "New build from the published simulation source; not the historical binary"}
        save(output / "build-receipt.json", receipt)
        (output / "build-receipt.sha256").write_text(sha(output / "build-receipt.json") + "\n")
        save(output / "progress.json", {"state": "built", "updated_utc": utc(), "accepted_events": 0})
        print(f"Built new executable: {receipt['executable_sha256']}", flush=True)
    except BaseException as error:
        stopped(output, "build", error)
        raise


def verify_binary(output: Path, receipt: dict) -> None:
    require(sha(output / "build-receipt.json") == (output / "build-receipt.sha256").read_text().strip(),
            "Build receipt changed")
    require(receipt["status"] == "passed" and receipt["manifest_sha256"] == sha(output / "manifest.json"),
            "Build receipt belongs to a different or failed preparation")
    require(sha(output / "build/OpNovice2") == receipt["executable_sha256"], "Newly built executable changed")


def summarize(output: Path, manifest: dict, reports: list[dict]) -> None:
    expected_names = {case["name"] for case in manifest["tasks"]}
    require(len(reports) == len(expected_names) and {report["task"] for report in reports} == expected_names,
            "Task set incomplete; refusing a completed summary")
    require(sum(report["events"] for report in reports) == manifest["event_budget"], "Completed event budget mismatch")
    rows = []
    for x in POSITIONS:
        for geometry in ("flat", "dimple"):
            matched = [report for report in reports if report["x_mm"] == x and report["geometry"] == geometry]
            if not matched:
                continue
            n = sum(report["events"] for report in matched)
            g = sum(report["generated_photons"] for report in matched)
            d = sum(report["detected_photons"] for report in matched)
            rows.append({"geometry": geometry, "x_mm": x, "events": n, "mean_D": d/n,
                         "mean_G": g/n, "legacy_ratio_of_totals_D_over_G": d/g if g else None,
                         "optical_parent_births": sum(report["diagnostics"]["optical_parent_scintillation_births"]
                                                       for report in matched)})
    name = "line-summary.csv" if manifest["mode"] == "full" else "smoke-summary.csv"
    with (output / name).open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    save(output / "verification.json", {"status": "passed", "finished_utc": utc(),
         "mode": manifest["mode"], "accepted_tasks": len(reports), "accepted_events": manifest["event_budget"],
         "manifest_sha256": sha(output / "manifest.json"), "summary_sha256": sha(output / name),
         "scope": manifest["scope"], "reproducibility_boundary": manifest["reproducibility_boundary"], "reports": reports})


def run(output: Path, expected_manifest: str | None = None) -> None:
    from optical_audit import audit_case
    manifest = verify_snapshot(output, expected_manifest)
    receipt = json.loads((output / "build-receipt.json").read_text())
    verify_binary(output, receipt)
    require(not any((output / name).exists() for name in ("run-attempt.json", "STOP.json", "verification.json")),
            "Existing run attempt preserved; prepare a new output directory")
    for case in manifest["tasks"]:
        directory = output / "tasks" / case["name"]
        require(not any((directory / name).exists() for name in
                        ("simulation.log", "result.root", "result_summary.csv", "result.rng", "receipt.json", "audit.json")),
                f"Existing task output preserved: {case['name']}")
    with (output / "run-attempt.json").open("x") as handle:
        json.dump({"started_utc": utc(), "manifest_sha256": sha(output / "manifest.json")}, handle)
    reports, current = [], None
    try:
        environment = runtime_environment()
        save(output / "run-environment.json", {**environment, "image_metadata": manifest["image_metadata"]})
        require(sha(DATA_RECEIPT) == manifest["dataset_manifest_sha256"], "Geant4 dataset manifest differs from publication")
        subprocess.run(["sha256sum", "--check", "--status", str(DATA_RECEIPT)], check=True)
        save(output / "data-preflight.json", {"status": "passed", "checked_utc": utc(),
                                             "dataset_manifest_sha256": sha(DATA_RECEIPT)})
        for case in manifest["tasks"]:
            current = case["name"]
            verify_snapshot(output, expected_manifest)
            verify_binary(output, receipt)
            directory = output / "tasks" / current
            save(output / "progress.json", {"state": "running", "current_task": current,
                 "accepted_tasks": len(reports), "accepted_events": sum(report["events"] for report in reports),
                 "event_budget": manifest["event_budget"], "updated_utc": utc()})
            started = time.monotonic()
            with (directory / "simulation.log").open("xb") as log:
                try:
                    result = subprocess.run([str(output / "build/OpNovice2"), "run.mac"], cwd=directory,
                        stdout=log, stderr=subprocess.STDOUT, timeout=manifest["task_timeout_seconds"])
                    code, timed_out = result.returncode, False
                except subprocess.TimeoutExpired:
                    code, timed_out = None, True
            save(directory / "receipt.json", {"task": current, "exit_code": code, "timed_out": timed_out,
                 "ended_utc": utc(), "elapsed_seconds": time.monotonic()-started,
                 "executable_sha256": sha(output / "build/OpNovice2"),
                 "macro_sha256": sha(directory / "run.mac"), "log_sha256": sha(directory / "simulation.log")})
            verify_snapshot(output, expected_manifest)
            verify_binary(output, receipt)
            report = audit_case(directory, case, code)
            report.update(task=current, block=case["block"])
            save(directory / "audit.json", report)
            reports.append(report)
            print(f"Accepted {len(reports)}/{len(manifest['tasks'])}: {current} ({case['events']} events)", flush=True)
        summarize(output, manifest, reports)
        save(output / "progress.json", {"state": "completed", "accepted_tasks": len(reports),
             "accepted_events": manifest["event_budget"], "event_budget": manifest["event_budget"], "updated_utc": utc()})
        print(f"Completed {manifest['mode']}: {manifest['event_budget']} audited events", flush=True)
    except BaseException as error:
        stopped(output, "run", error, current_task=current, accepted_tasks=len(reports),
                accepted_events=sum(report["events"] for report in reports))
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("--output", required=True, type=Path)
    preparation.add_argument("--mode", required=True, choices=("smoke", "full"))
    preparation.add_argument("--image-metadata", type=Path, help="Optional host-provided JSON image inspection record")
    for name in ("build", "run"):
        command = commands.add_parser(name)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--manifest-sha256", help="Optional external checksum; otherwise use manifest.sha256")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.action == "prepare":
        prepare(output, args.mode, args.image_metadata)
    elif args.action == "build":
        build(output, args.manifest_sha256)
    else:
        run(output, args.manifest_sha256)


if __name__ == "__main__":
    main()
