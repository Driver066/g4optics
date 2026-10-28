#!/usr/bin/env python3
"""Prepare/verify a fixed scientific pilot; execute only explicitly named tasks.

Preparation never starts Geant4 or a container. Run the frozen copy after
preparation. Each task has exactly one attempt, with no retry/overwrite path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import socket
import subprocess
import time
import uuid

import engineering
import model
import pilot_model

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "issue9-dimple-pilot-inputs-v1"
MANIFEST = "input-manifest.json"
DEPENDENCIES = (
    "tools/dimple_mechanism/pilot.py", "tools/dimple_mechanism/pilot_model.py",
    "tools/dimple_mechanism/pilot_audit.py", "tools/dimple_mechanism/engineering.py",
    "tools/dimple_mechanism/model.py", "tools/dimple_mechanism/audit.py",
    "tools/dimple_study/optical_audit.py", "tools/layout_study/audit.py",
)
OUTPUT_NAMES = {"simulation.log", "result.root", "result_summary.csv", "result.rng"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def save(path: Path, value: dict) -> None:
    """Exclusive, fully written publication; readers never observe partial JSON."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # Atomic and refuses an existing destination.
    finally:
        temporary.unlink(missing_ok=True)


def checked_path(base: Path, relative: str) -> Path:
    path = Path(relative)
    require(not path.is_absolute() and ".." not in path.parts, "Unsafe frozen path")
    result = base / path
    require(result.resolve().is_relative_to(base.resolve()), "Frozen path escapes its batch")
    return result


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def application_paths() -> set[str]:
    app = ROOT / "test/OpNovice2"
    paths = [app / "CMakeLists.txt", app / "OpNovice2.cc", *app.glob("src/*.cc"),
             *app.glob("include/*.hh"), *app.glob("*.mac"), *app.glob("*.out")]
    return {str(path.relative_to(ROOT)) for path in paths}


def accepted_files(accepted: Path) -> tuple[dict, dict[str, Path]]:
    """Check accepted frozen evidence, avoiding the unused regression.py cleanup."""
    manifest = engineering.verify_prepared(accepted, verify_runtime=False)
    build = engineering.verify_build(accepted, manifest)
    validation_path = ROOT / "tools/dimple_mechanism/validation.json"
    validation = read(validation_path)
    require(validation["status"] == "passed" and validation["source_sha256"] == manifest["source_sha256"],
            "Accepted publication/source identity mismatch")
    require(build["geant4_version"] == "11.4.2" and build["executable_sha256"] == validation["executable_sha256"],
            "Accepted executable/version mismatch")
    require(manifest["image_id"] == validation["image_id"] and
            manifest["dataset_manifest_sha256"] == validation["dataset_manifest_sha256"],
            "Accepted image/dataset mismatch")
    require(application_paths() == set(manifest["source_sha256"]), "Current application inventory changed")
    for relative, expected in manifest["source_sha256"].items():
        require(sha(ROOT / relative) == expected, f"Current accepted application/template changed: {relative}")
    for relative in DEPENDENCIES[3:]:
        require(sha(ROOT / relative) == manifest["tool_sha256"][relative],
                f"Executed engineering dependency changed: {relative}")
    files = {f"accepted/source/{relative}": accepted / "source" / relative
             for relative in {**manifest["source_sha256"], **manifest["tool_sha256"]}}
    files["proofs/validation.json"] = validation_path
    for relative, record in validation["evidence"].items():
        # These publication records bind the accepted engineering and regression
        # results. No historical regression tool is executed or weakened.
        path = ROOT / relative
        require(sha(path) == record["sha256"], f"Published acceptance evidence changed: {relative}")
        files[f"proofs/{relative}"] = path
    for name in ("manifest.json", "manifest.sha256", "source.patch", "optical-template.mac",
                 "dataset-SHA256SUMS", "build-receipt.json", "build.log",
                 "geometry-execution.json", "smoke-execution.json",
                 "geometry-verification.json", "smoke-verification.json"):
        files[f"accepted/{name}"] = accepted / name
    files["bin/OpNovice2"] = accepted / "build/OpNovice2"
    for stage, expected_events in (("geometry", 0), ("smoke", 42)):
        gate = read(accepted / f"{stage}-verification.json")
        selected = [case for case in manifest["cases"] if case["stage"] == stage]
        require(gate["status"] == "passed" and gate["events"] == expected_events and
                gate["manifest_sha256"] == sha(accepted / "manifest.json") and
                gate["executable_sha256"] == build["executable_sha256"] and
                [item["name"] for item in gate["cases"]] == [case["name"] for case in selected],
                f"Incomplete accepted {stage} gate")
        for case, receipt in zip(selected, gate["cases"]):
            directory = accepted / "cases" / case["name"]
            require(read(directory / "receipt.json") == receipt and receipt["passed"] is True and
                    receipt["macro_sha256"] == case["macro_sha256"] and
                    receipt["log_sha256"] == sha(directory / "simulation.log") and
                    receipt["audit"]["status"] == "passed", "Accepted case receipt changed")
            names = {"run.mac", "simulation.log", "receipt.json"}
            if stage == "smoke":
                for name, key in (("result.root", "root_sha256"), ("result_summary.csv", "summary_sha256"),
                                  ("result.rng", "rng_sha256")):
                    require(sha(directory / name) == receipt["audit"][key], f"Accepted output changed: {name}")
                    names.add(name)
                require(receipt["events"] == receipt["audit"]["events"] == 2,
                        "Accepted smoke events changed")
            for name in names:
                files[f"accepted/cases/{case['name']}/{name}"] = directory / name
    return manifest, files


def historical_seeds(value) -> set[int]:
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if "seed" in key.lower() and isinstance(item, list) and all(type(x) is int for x in item):
                result.update(item)
            result.update(historical_seeds(item))
    elif isinstance(value, list):
        for item in value:
            result.update(historical_seeds(item))
    return result


def validate_seed_exclusions(registry: dict, paths: dict[str, Path]) -> None:
    require(registry.get("schema") == "issue9-pilot-seed-exclusions-v1" and registry.get("sources"),
            "Seed exclusion registry needs explicit historical sources")
    values = registry.get("seeds", [])
    require(values and all(type(x) is int and x > 0 for x in values) and len(set(values)) == len(values),
            "Seed exclusions must be unique positive integers")
    union = set()
    for source in registry["sources"]:
        path = paths[source["manifest"]]
        require(sha(path) == source["manifest_sha256"], "Historical seed manifest changed")
        actual = historical_seeds(read(path))
        for name, expected in source.get("verified_geometry_macro_sha256", {}).items():
            macro = paths[name]
            require(sha(macro) == expected, "Historical seed macro changed")
            for line in macro.read_text().splitlines():
                tokens = line.partition("#")[0].split()
                if tokens and tokens[0] == "/random/setSeeds":
                    actual.update(int(item) for item in tokens[1:])
        require(actual == set(source["seeds"]), "Historical seed list is not supported by its frozen sources")
        union.update(actual)
    require(union == set(values), "Exclusion union differs from the historical sources")
    fresh = [seed for task in pilot_model.tasks() for seed in task["seeds"]]
    require(len(fresh) == len(set(fresh)) == 700 and not set(fresh).intersection(union),
            "Pilot seeds collide internally or with historical seeds")


def prepare(batch: Path, accepted: Path, exclusions_path: Path) -> dict:
    require(not batch.exists(), "Batch already exists; overwriting is forbidden")
    accepted_manifest, copies = accepted_files(accepted)
    registry = read(exclusions_path)
    history_paths, history_frozen = {}, {}
    for index, source in enumerate(registry.get("sources", [])):
        names = [source["manifest"], *source.get("verified_geometry_macro_sha256", {})]
        for subindex, name in enumerate(names):
            path = Path(name)
            history_paths[name] = path if path.is_absolute() else ROOT / path
            relative = f"seed-history/{index:02d}-{subindex:02d}{path.suffix}"
            history_frozen[name] = relative
            copies[relative] = history_paths[name]
    validate_seed_exclusions(registry, history_paths)
    template = (accepted / "optical-template.mac").read_text()
    tasks = pilot_model.tasks()
    macros = {task["name"]: pilot_model.macro(template, task) for task in tasks}
    require(len(tasks) == 350 and sum(task["events"] for task in tasks) == 35000,
            "The registered scientific task/event budget changed")
    copies["seed-exclusions.json"] = exclusions_path
    copies["protocol/PILOT.md"] = ROOT / "tools/dimple_mechanism/PILOT.md"
    for relative in DEPENDENCIES:
        copies[f"source/{relative}"] = ROOT / relative
    for relative in accepted_manifest["source_sha256"]:
        copies[f"source/{relative}"] = ROOT / relative
    metadata = dict(head_commit=git("rev-parse", "HEAD").decode().strip(),
                    git_status=git("status", "--porcelain").decode(), accepted_path=str(accepted))
    patch = git("diff", "--binary", "HEAD")
    batch.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for relative, path in copies.items():
        target = checked_path(batch, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[relative] = sha(target)
    for relative, expected in accepted_manifest["source_sha256"].items():
        require(hashes[f"source/{relative}"] == expected, f"Application changed while freezing: {relative}")
    (batch / "bin/OpNovice2").chmod(0o555)
    (batch / "source.patch").write_bytes(patch)
    hashes["source.patch"] = sha(batch / "source.patch")
    for task in tasks:
        directory = batch / "tasks" / task["name"]
        directory.mkdir(parents=True)
        (directory / "run.mac").write_text(macros[task["name"]])
        task["macro_sha256"] = sha(directory / "run.mac")
        hashes[f"tasks/{task['name']}/run.mac"] = task["macro_sha256"]
    manifest = dict(schema=SCHEMA, model_id=pilot_model.MODEL_ID, **metadata,
        total_tasks=350, total_events=35000, events_per_task=100,
        tasks=tasks, positions=pilot_model.positions(), source_z_mm=18.,
        image_id=accepted_manifest["image_id"], dataset_manifest_sha256=accepted_manifest["dataset_manifest_sha256"],
        executable_sha256=hashes["bin/OpNovice2"], seed_history_files=history_frozen,
        analysis_contract_sha256=hashes["protocol/PILOT.md"],
        running_dependency_sha256={relative: hashes[f"source/{relative}"] for relative in DEPENDENCIES},
        frozen_file_sha256=hashes, accepted_manifest_sha256=hashes["accepted/manifest.json"],
        scope="Prepared scientific pilot only; 350 separately claimed 100-event tasks. No execution authorized by preparation.")
    save(batch / MANIFEST, manifest)
    (batch / "input-manifest.sha256").write_text(sha(batch / MANIFEST) + "\n")
    verify(batch)
    return manifest


def verify(batch: Path, expected_manifest_sha256: str | None = None) -> dict:
    if expected_manifest_sha256 is not None:
        require(re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256) is not None and
                sha(batch / MANIFEST) == expected_manifest_sha256, "Explicit input manifest identity mismatch")
    manifest = read(batch / MANIFEST)
    require(sha(batch / MANIFEST) == (batch / "input-manifest.sha256").read_text().strip(), "Input manifest changed")
    require(manifest["schema"] == SCHEMA and manifest["model_id"] == pilot_model.MODEL_ID,
            "Wrong frozen scientific pilot identity")
    require((manifest["total_tasks"], manifest["total_events"], manifest["events_per_task"]) == (350, 35000, 100),
            "Frozen pilot budget changed")
    require(manifest["positions"] == pilot_model.positions(), "Frozen pilot positions changed")
    frozen = manifest["frozen_file_sha256"]
    for relative, expected in frozen.items():
        path = checked_path(batch, relative)
        require(path.is_file() and not path.is_symlink() and sha(path) == expected,
                f"Frozen input/source/binary changed: {relative}")
    for dirname in ("source", "accepted", "proofs", "seed-history", "bin", "protocol"):
        observed = {str(path.relative_to(batch)) for path in (batch / dirname).rglob("*") if path.is_file()}
        require(observed == {name for name in frozen if name.startswith(dirname + "/")},
                f"Frozen {dirname} inventory changed")
    require(manifest["running_dependency_sha256"] == {relative: frozen[f"source/{relative}"] for relative in DEPENDENCIES},
            "Executed dependency registry changed")
    for relative, expected in manifest["running_dependency_sha256"].items():
        require(sha(ROOT / relative) == expected, f"Running dependency changed: {relative}; use the frozen runner")
    require(sha(batch / "bin/OpNovice2") == manifest["executable_sha256"], "Executable identity mismatch")
    require(sha(batch / "protocol/PILOT.md") == manifest["analysis_contract_sha256"], "Scientific analysis contract changed")
    template = (batch / "accepted/optical-template.mac").read_text()
    expected_tasks = pilot_model.tasks()
    require(len(manifest["tasks"]) == len(expected_tasks), "Task inventory changed")
    require({path.name for path in (batch / "tasks").iterdir()} == {task["name"] for task in expected_tasks},
            "Task directory inventory changed")
    for task, expected in zip(manifest["tasks"], expected_tasks):
        pilot_model.validate_task(task)
        require({key: value for key, value in task.items() if key != "macro_sha256"} == expected,
                "Task registration changed")
        path = batch / "tasks" / task["name"] / "run.mac"
        require(sha(path) == task["macro_sha256"] and path.read_text() == pilot_model.macro(template, task),
                f"Task macro changed: {task['name']}")
    paths = {name: checked_path(batch, relative) for name, relative in manifest["seed_history_files"].items()}
    validate_seed_exclusions(read(batch / "seed-exclusions.json"), paths)
    return manifest


def runtime(manifest: dict) -> dict:
    result = engineering.environment(manifest)
    require(result["machine"] in ("aarch64", "arm64") and result["platform"].startswith("Linux"),
            "The accepted executable requires the pinned linux/arm64 runtime")
    require(bool(os.statvfs("/g4data").f_flag & os.ST_RDONLY), "Dataset mount must be read-only")
    result["dataset_mount_readonly"] = True
    return result


def preflight(batch: Path, manifest_sha256: str) -> dict:
    manifest = verify(batch, manifest_sha256)
    environment = runtime(manifest)
    identity = sha(batch / MANIFEST)
    save(batch / "preflight-claim.json", dict(input_manifest_sha256=identity, pid=os.getpid(), host=socket.gethostname()))
    inventory = batch / "accepted/dataset-SHA256SUMS"
    count = 0
    try:
        with (batch / "preflight.log").open("x") as output:
            for line in inventory.read_text().splitlines():
                match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
                require(match is not None, "Malformed dataset checksum inventory")
                expected, name = match.groups()
                path = Path(name)
                require(path.is_absolute() and path.resolve().is_relative_to(Path("/g4data")), "Dataset path outside /g4data")
                require(sha(path) == expected, f"Dataset checksum failed: {name}")
                output.write(f"{name}: OK\n")
                count += 1
        require(count > 0, "Dataset inventory is empty")
        verify(batch, identity)
        report = dict(status="passed", input_manifest_sha256=identity, environment=environment,
                      checked_files=count, inventory_sha256=sha(inventory), log_sha256=sha(batch / "preflight.log"),
                      scope="Full checksum preflight of the read-only dataset mount; no simulation events")
        save(batch / "preflight.json", report)
        return report
    except BaseException as exc:
        save(batch / "preflight-failure.json", dict(input_manifest_sha256=identity, error=f"{type(exc).__name__}: {exc}"))
        raise


def verify_preflight(batch: Path, manifest: dict, environment: dict | None = None) -> dict:
    require(not (batch / "preflight-failure.json").exists(), "A failed preflight cannot authorize execution")
    report = read(batch / "preflight.json")
    require(report["status"] == "passed" and report["input_manifest_sha256"] == sha(batch / MANIFEST) and
            report["inventory_sha256"] == manifest["dataset_manifest_sha256"] and
            report["log_sha256"] == sha(batch / "preflight.log") and report["checked_files"] > 0,
            "Missing/changed full dataset preflight")
    if environment is not None:
        require(environment == report["environment"], "Runtime differs from the completed preflight")
    return report


def find_task(manifest: dict, task_id: str) -> dict:
    task = next((task for task in manifest["tasks"] if task["name"] == task_id), None)
    require(task is not None, "Unknown task id; only an exact registered task can execute")
    return task


def claim_task(batch: Path, task: dict, identity: str, environment: dict, timeout: int = 1800) -> Path:
    directory = batch / "tasks" / task["name"]
    require({path.name for path in directory.iterdir()} == {"run.mac"}, "Existing or contaminated task attempt; retry forbidden")
    # Exclusive creation arbitrates concurrent requests for this one task only.
    with (directory / "attempt.json").open("x") as handle:
        json.dump(dict(task_id=task["name"], input_manifest_sha256=identity,
                       macro_sha256=task["macro_sha256"], environment=environment, timeout_seconds=timeout,
                       pid=os.getpid(), host=socket.gethostname(), started_unix=time.time()), handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return directory


def run_task(batch: Path, task_id: str, manifest_sha256: str, timeout: int = 1800) -> dict:
    manifest = verify(batch, manifest_sha256)
    task = find_task(manifest, task_id)
    require(timeout > 0, "Task timeout must be positive")
    require(not (batch / "finalization.json").exists(), "The pilot is already finalized")
    environment = runtime(manifest)
    verify_preflight(batch, manifest, environment)
    identity = sha(batch / MANIFEST)
    directory = claim_task(batch, task, identity, environment, timeout)
    started = time.monotonic()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        with (directory / "simulation.log").open("xb") as output:
            result = subprocess.run([str(batch / "bin/OpNovice2"), "run.mac"], cwd=directory,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        verify(batch, identity)
        require(runtime(manifest) == environment, "Runtime changed during the task")
        import pilot_audit
        report = pilot_audit.audit_case(directory, task, result.returncode)
        require(report["status"] == "passed" and report["accepted"] is True and report["events"] == 100,
                "Task did not pass its scientific output audit")
        require({path.name for path in directory.iterdir()} == {"run.mac", "attempt.json", *OUTPUT_NAMES},
                "Unexpected task output inventory")
        receipt = dict(status="passed", task_id=task["name"], input_manifest_sha256=identity,
            executable_sha256=manifest["executable_sha256"], macro_sha256=task["macro_sha256"],
            attempt_sha256=sha(directory / "attempt.json"), process_exit=result.returncode, events=100, timeout_seconds=timeout,
            elapsed_seconds=time.monotonic() - started, environment=environment, audit=report,
            output_sha256={name: sha(directory / name) for name in sorted(OUTPUT_NAMES)})
        save(directory / "receipt.json", receipt)
        return receipt
    except BaseException as exc:
        save(directory / "failure.json", dict(status="failed", task_id=task["name"], input_manifest_sha256=identity,
             error=f"{type(exc).__name__}: {exc}", elapsed_seconds=time.monotonic() - started))
        raise


def verify_receipt(batch: Path, manifest: dict, task: dict, reaudit: bool = False) -> dict:
    directory = batch / "tasks" / task["name"]
    receipt = read(directory / "receipt.json")
    require({path.name for path in directory.iterdir()} == {"run.mac", "attempt.json", "receipt.json", *OUTPUT_NAMES},
            "Incomplete/failed/contaminated task output inventory")
    require(receipt["status"] == "passed" and receipt["task_id"] == task["name"] and receipt["events"] == 100 and
            receipt["process_exit"] == 0 and receipt["input_manifest_sha256"] == sha(batch / MANIFEST) and
            receipt["executable_sha256"] == manifest["executable_sha256"] and
            receipt["macro_sha256"] == task["macro_sha256"] and
            receipt["attempt_sha256"] == sha(directory / "attempt.json"), "Task receipt identity mismatch")
    claim = read(directory / "attempt.json")
    require(claim["task_id"] == task["name"] and claim["input_manifest_sha256"] == receipt["input_manifest_sha256"] and
            claim["macro_sha256"] == task["macro_sha256"] and claim["environment"] == receipt["environment"] and
            type(receipt["timeout_seconds"]) is int and receipt["timeout_seconds"] > 0 and
            claim["timeout_seconds"] == receipt["timeout_seconds"],
            "Task attempt identity mismatch")
    require(set(receipt["output_sha256"]) == OUTPUT_NAMES, "Task output registry mismatch")
    for name, expected in receipt["output_sha256"].items():
        require(sha(directory / name) == expected, f"Accepted task output changed: {name}")
    require(receipt["audit"]["status"] == "passed" and receipt["audit"]["accepted"] is True and
            receipt["audit"]["events"] == 100, "Task audit is not accepted")
    if reaudit:
        import pilot_audit
        require(pilot_audit.audit_case(directory, task, 0) == receipt["audit"], "Fresh task audit differs from its receipt")
    return receipt


def status(batch: Path, expected_manifest_sha256: str | None = None) -> dict:
    manifest = verify(batch, expected_manifest_sha256)
    counts = dict(unstarted=0, attempted=0, failed=0, accepted=0, invalid=0)
    details = {}
    for task in manifest["tasks"]:
        directory = batch / "tasks" / task["name"]
        if (directory / "failure.json").exists():
            state = "failed"
        elif (directory / "attempt.json").exists() and any(
                re.fullmatch(r"\.receipt\.json\.[0-9a-f]{32}\.tmp", path.name) for path in directory.iterdir()):
            state = "attempted"  # Atomic receipt publication is still completing.
        elif (directory / "receipt.json").exists():
            try:
                verify_receipt(batch, manifest, task)
                state = "accepted"
            except (ValueError, KeyError, OSError) as exc:
                state = "invalid"
                details[task["name"]] = str(exc)
        elif (directory / "attempt.json").exists():
            state = "attempted"  # Deliberately does not claim a process is live.
        else:
            state = "unstarted" if {p.name for p in directory.iterdir()} == {"run.mac"} else "invalid"
        counts[state] += 1
    return dict(input_manifest_sha256=sha(batch / MANIFEST), total_tasks=350, counts=counts,
                accepted_events=100 * counts["accepted"], details=details,
                finalized=(batch / "finalization.json").exists())


def finalize(batch: Path, manifest_sha256: str) -> dict:
    manifest = verify(batch, manifest_sha256)
    preflight_report = verify_preflight(batch, manifest)
    require(not (batch / "finalization.json").exists(), "Pilot already finalized")
    results = []
    for task in manifest["tasks"]:
        receipt = verify_receipt(batch, manifest, task, reaudit=True)
        require(receipt["environment"] == preflight_report["environment"], "Task runtime differs from pilot preflight")
        results.append(dict(task_id=task["name"], events=receipt["events"],
                            receipt_sha256=sha(batch / "tasks" / task["name"] / "receipt.json")))
    require(len(results) == 350 and sum(item["events"] for item in results) == 35000,
            "Incomplete scientific pilot; failed/missing events cannot be replaced")
    verify(batch, manifest_sha256)
    report = dict(status="passed", input_manifest_sha256=sha(batch / MANIFEST), total_tasks=350,
                  total_events=35000, tasks=results, preflight_sha256=sha(batch / "preflight.json"),
                  scope="Complete registered pilot accounting only; scientific effect estimates require separate analysis")
    save(batch / "finalization.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "status", "preflight", "run-task", "finalize"))
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--accepted", type=Path)
    parser.add_argument("--seed-exclusions", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    batch = args.batch.resolve()
    if args.action == "prepare":
        if not args.accepted or not args.seed_exclusions or args.task_id or args.manifest_sha256:
            parser.error("prepare requires --accepted and --seed-exclusions, and no task id")
        result = prepare(batch, args.accepted.resolve(), args.seed_exclusions.resolve())
        print(json.dumps(dict(status="prepared", tasks=result["total_tasks"], events=result["total_events"],
                              input_manifest_sha256=sha(batch / MANIFEST))))
        return
    if args.accepted or args.seed_exclusions:
        parser.error("Accepted inputs and seeds are frozen at preparation")
    if (args.action == "run-task") != bool(args.task_id):
        parser.error("Only run-task accepts and requires one explicit --task-id")
    if args.action in ("preflight", "run-task", "finalize") and not args.manifest_sha256:
        parser.error("preflight, run-task, and finalize require the reviewed --manifest-sha256")
    if args.action == "verify":
        verify(batch, args.manifest_sha256)
        result = dict(status="verified", input_manifest_sha256=sha(batch / MANIFEST))
    elif args.action == "status":
        result = status(batch, args.manifest_sha256)
    elif args.action == "preflight":
        result = preflight(batch, args.manifest_sha256)
    elif args.action == "run-task":
        result = run_task(batch, args.task_id, args.manifest_sha256, args.timeout)
    else:
        result = finalize(batch, args.manifest_sha256)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
