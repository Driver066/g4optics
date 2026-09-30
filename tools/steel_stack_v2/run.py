#!/usr/bin/env python3
"""Freeze, build, and audit serial local stack-v2 tasks; never submit OSC work."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

REPO = Path(os.environ.get("STEEL_STACK_V2_REPOSITORY_ROOT", Path(__file__).resolve().parents[2])).resolve()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "local"))
import g4env as local
from acceptance import STAGES, compare_runs, registered_tasks, scan_args, single_file
from model import make_configuration, make_matrix, prepare_tasks

# A frozen runner imports the frozen environment helper, while paths to the
# mounted workspace and the previously accepted installation stay explicit.
local.REPO = REPO
local.APP = REPO / "test/OpNovice2"
local.ENVROOT = REPO / "outputs/environment"
local.ACTIVE = local.ENVROOT / "active.json"
local.IMAGE_LOCK = local.ENVROOT / "image-lock.json"
local.PYTHON = local.APP / ".venv-analysis/bin/python"

PYTHON = REPO / "test/OpNovice2/.venv-analysis/bin/python"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    local.save(path, value)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def checked_batch(value):
    path = Path(value).resolve()
    path.relative_to(REPO / "outputs/steel_stack_v2")
    return path


def journal(batch, event, **fields):
    with (batch / "journal.jsonl").open("a") as stream:
        stream.write(json.dumps(dict(utc=stamp(), event=event, **fields)) + "\n")


def verify_analysis_environment(batch):
    installed = sorted((distribution.metadata["Name"], distribution.version)
                       for distribution in importlib.metadata.distributions())
    identity = dict(python_executable=str(Path(sys.executable).resolve()), python_version=sys.version,
                    packages=[list(item) for item in installed],
                    requirements_lock_sha256=sha(REPO / "test/OpNovice2/requirements-analysis.lock.txt"))
    path = batch / "analysis-environment.json"
    if path.exists() and read(path) != identity:
        raise RuntimeError("Host analysis environment changed since batch preparation")
    if not path.exists():
        save(path, identity)
    return identity


def preserve_before(batch):
    path = batch / "preserved-before.json"
    if path.exists():
        return
    names = ["outputs/environment/active.json"]
    app = "test/OpNovice2/"
    names += [app + item for item in ("scan_latest", "run_config.json", "points.csv", "efficiency_map.csv")]
    for directory in (local.CONFIG["build_dir"], local.CONFIG["legacy_build_dir"]):
        names += [app + directory + "/" + item for item in ("OpNovice2", "CMakeCache.txt")]
    state = {}
    for name in names:
        item = REPO / name
        entry = dict(exists=item.exists(), symlink=item.is_symlink())
        if item.is_symlink():
            entry["target"] = str(item.readlink())
        elif item.is_file():
            entry["sha256"] = sha(item)
        state[name] = entry
    save(path, state)


def source_names():
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                                    cwd=REPO, text=True).splitlines()
    return [name for name in names if not name.startswith("outputs/") and (REPO / name).is_file()]


def verify_source(role_dir):
    manifest = read(role_dir / "source-manifest.json")
    source = role_dir / "source"
    actual = {str(p.relative_to(source)): sha(p) for p in source.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    if actual != manifest["source_files"]:
        raise RuntimeError("Frozen source was modified: " + str(source))
    return manifest


def snapshot(batch, role):
    batch.mkdir(parents=True, exist_ok=True)
    preserve_before(batch)
    role_dir = batch / role
    if (role_dir / "source-manifest.json").exists():
        verify_source(role_dir)
        return
    source = role_dir / "source"
    source.mkdir(parents=True, exist_ok=False)
    files = {}
    for name in source_names():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
        files[name] = sha(target)
    manifest = dict(schema_version="steel-stack-v2-frozen-source-v1", created_utc=stamp(),
                    git_commit=local.run(["git", "rev-parse", "HEAD"], capture=True),
                    git_status=local.run(["git", "status", "--porcelain", "--untracked-files=all"], capture=True),
                    source_files=files)
    save(role_dir / "source-manifest.json", manifest)
    journal(batch, "source-frozen", role=role, files=len(files),
            manifest_sha256=sha(role_dir / "source-manifest.json"))


def environment_identity():
    local.start_new()
    result = dict(image_id=local.inspect(local.CONTAINER)["Image"],
                  geant4_version=local.dexec(["geant4-config", "--version"], capture=True),
                  architecture=local.dexec(["uname", "-m"], capture=True),
                  datasets=local.dexec(["geant4-config", "--check-datasets"], capture=True),
                  dataset_manifest_sha256=sha(local.HERE / "geant4-11.4.2-datasets.json"),
                  dataset_receipt_sha256=sha(Path(local.CONFIG["data_root"]) / "geant4-datasets-receipt.json"))
    if result["geant4_version"] != "11.4.2" or result["architecture"] != "aarch64":
        raise RuntimeError("Local acceptance requires the accepted Geant4 11.4.2 ARM64 environment")
    if len(result["datasets"].splitlines()) != 12 or any(
            " INSTALLED " not in line for line in result["datasets"].splitlines()):
        raise RuntimeError("All twelve frozen datasets must be installed")
    return result


def build(batch, role):
    role_dir = batch / role
    verify_source(role_dir)
    identity = environment_identity()
    if (role_dir / "environment.json").exists() and read(role_dir / "environment.json") != identity:
        raise RuntimeError("Reference/candidate environment identity changed")
    save(role_dir / "environment.json", identity)
    receipt_path = role_dir / "binary.json"
    if receipt_path.exists():
        verify_binary(batch, role, identity)
        return
    build_dir = role_dir / "build"
    local.dexec(["cmake", "-S", local.cpath(role_dir / "source/test/OpNovice2"),
                 "-B", local.cpath(build_dir), "-DCMAKE_BUILD_TYPE=Release",
                 "-DWITH_GEANT4_UIVIS=ON", "-DGeant4_DIR=/opt/geant4/lib/cmake/Geant4"],
                log=role_dir / "build.log")
    local.dexec(["cmake", "--build", local.cpath(build_dir), "-j4"], log=role_dir / "build.log")
    verify_source(role_dir)
    executable = build_dir / "OpNovice2"
    links = local.dexec(["ldd", local.cpath(executable)], capture=True)
    if "not found" in links or "libG4" not in links:
        raise RuntimeError("Incomplete Geant4 dynamic linkage")
    (role_dir / "dynamic-libraries.txt").write_text(links + "\n")
    save(receipt_path, dict(path=str(executable), sha256=sha(executable),
                           cmake_cache_sha256=sha(build_dir / "CMakeCache.txt"),
                           source_manifest_sha256=sha(role_dir / "source-manifest.json"), **identity))
    with tarfile.open(role_dir / "frozen-source.tar.gz", "w:gz") as archive:
        archive.add(role_dir / "source", arcname="source")
    journal(batch, "binary-frozen", role=role, executable_sha256=sha(executable))


def verify_binary(batch, role, identity=None):
    role_dir = batch / role
    verify_source(role_dir)
    receipt = read(role_dir / "binary.json")
    if sha(receipt["path"]) != receipt["sha256"]:
        raise RuntimeError("Frozen executable changed")
    if sha(role_dir / "build/CMakeCache.txt") != receipt["cmake_cache_sha256"]:
        raise RuntimeError("Frozen build configuration changed")
    identity = identity or environment_identity()
    if identity != read(role_dir / "environment.json"):
        raise RuntimeError("Runtime environment changed since the frozen build")
    recorded = receipt.get("source_manifest_sha256")
    if recorded and recorded != sha(role_dir / "source-manifest.json"):
        raise RuntimeError("Build/source identity mismatch")
    return receipt


def prepare(batch, matrix, *, events=None, blocks=None, campaign_seed=None, budget=None):
    batch.mkdir(parents=True, exist_ok=True)
    preserve_before(batch)
    verify_analysis_environment(batch)
    if (batch / "registered-tasks.json").exists():
        raise RuntimeError("Task registry already exists; use a new batch")
    if matrix == "acceptance":
        tasks = registered_tasks()
        for task in tasks:
            if task["preset"] == "steel-module-stack-v2":
                task["config"] = make_configuration(task["layout"], task["tile_thickness_mm"],
                                                     task["readout_gap_mm"])
            task["block"] = 0
            task["independent_sample"] = False
            task["reproducibility_check"] = bool(task["compare_to"])
        purpose = "engineering-acceptance-not-scientific-evidence"
    else:
        if any(value is None for value in (events, blocks, campaign_seed, budget)):
            raise ValueError("Scientific preparation requires explicit events, blocks, campaign seed and total-event budget")
        tasks = prepare_tasks(make_matrix(matrix, [0.5, 1.0]), events_per_task=events, blocks=blocks,
                              campaign_seed=campaign_seed, total_event_budget=budget)
        for task in tasks:
            config = task["config"]
            task.update(role="candidate", preset="steel-module-stack-v2", accounting=True,
                        layout=config["layout"], tile_thickness_mm=config["tile_thickness_mm"],
                        readout_gap_mm=config["gap_mm"], compare_to=None)
        purpose = "science-prepared-not-executed"
    save(batch / "registered-tasks.json", tasks)
    save(batch / "tasks.json", tasks)
    save(batch / "manifest.json", dict(schema_version="steel-stack-v2-local-batch-v1", created_utc=stamp(),
                                       matrix=matrix, purpose=purpose, execution="local-serial",
                                       geant4_version="11.4.2", update_latest=False,
                                       task_count=len(tasks), event_count=sum(t["events"] for t in tasks),
                                       task_timeout_seconds=900,
                                       registered_tasks_sha256=sha(batch / "registered-tasks.json")))
    journal(batch, "tasks-registered", matrix=matrix, tasks=len(tasks), events=sum(t["events"] for t in tasks))


def verify_registry(batch):
    manifest = read(batch / "manifest.json")
    if sha(batch / "registered-tasks.json") != manifest["registered_tasks_sha256"]:
        raise RuntimeError("Registered task identities were changed")
    originals = read(batch / "registered-tasks.json")
    tasks = read(batch / "tasks.json")
    if len(tasks) != len(originals):
        raise RuntimeError("Task registry cardinality changed")
    for a, b in zip(originals, tasks):
        if any(b.get(key) != value for key, value in a.items()):
            raise RuntimeError("Task scientific identity changed: " + a["task_id"])
    return manifest, tasks


def registered_task_hash(batch, task_id):
    entries = [t for t in read(batch / "registered-tasks.json") if t["task_id"] == task_id]
    if len(entries) != 1:
        raise ValueError("Task identity is not unique in the immutable registry")
    return hashlib.sha256(json.dumps(entries[0], sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def verify_controller_snapshot(batch):
    frozen = batch / "candidate/source/tools"
    actual = {"steel_stack_v2/run.py": Path(__file__),
              "steel_stack_v2/acceptance.py": Path(sys.modules["acceptance"].__file__),
              "steel_stack_v2/model.py": Path(sys.modules["model"].__file__),
              "local/g4env.py": Path(local.__file__)}
    for name, path in actual.items():
        if not (frozen / name).is_file() or sha(path) != sha(frozen / name):
            raise RuntimeError("Use the controller and dependencies from the frozen candidate: " + name)


def compare_frozen(batch, left_dir, right_dir):
    source = batch / "candidate/source/tools/steel_stack_v2"
    code = ("import sys,json; sys.path.insert(0,sys.argv[1]); from acceptance import compare_runs; "
            "print(json.dumps(compare_runs(sys.argv[2],sys.argv[3])))")
    result = subprocess.run([str(PYTHON), "-B", "-c", code, str(source), str(left_dir), str(right_dir)],
                            text=True, capture_output=True)
    if result.returncode:
        raise ValueError("Frozen exact comparison failed: " + result.stderr.strip())
    report = json.loads(result.stdout)
    report["implementation_sha256"] = sha(source / "acceptance.py")
    return report


def basic_audit(run_dir, task, tool_source=None):
    import uproot
    root = single_file(run_dir, "outputs/*.root")
    with uproot.open(root) as data:
        if data["scan"].num_entries != task["events"]:
            raise ValueError("ROOT event count differs from registered task")
    single_file(run_dir, "outputs/*.rng-end.txt")
    config = read(Path(run_dir) / "run_config.json")
    macro = single_file(run_dir, "macros/*.mac")
    commands = [shlex.split(line, comments=True) for line in macro.read_text().splitlines()]
    seeds = [tokens[1:] for tokens in commands if tokens and tokens[0] == "/random/setSeeds"]
    beam = [tokens[1:] for tokens in commands if tokens and tokens[0] == "/run/beamOn"]
    if seeds != [[str(task["seed1"]), str(task["seed2"])]] or beam != [[str(task["events"])]]:
        raise ValueError("Executed macro seeds/event count differ from the registered task")
    summary = single_file(run_dir, "outputs/*_summary.csv")
    import csv
    with summary.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1 or int(rows[0]["events"]) != task["events"] or int(rows[0]["committed_events"]) != task["events"]:
        raise ValueError("Legacy summary has incomplete event counts")
    log = single_file(run_dir, "logs/*.log").read_text(errors="replace")
    if "geant4-11-04-patch-02" not in log:
        raise ValueError("Actual program did not report Geant4 11.4.2")
    # Geant4 emits this exact nonphysical warning for the preserved legacy
    # SetNtupleMerging(true) call in Serial mode. Do not exempt other W001s.
    def exception_filter(match):
        block = match.group(0)
        allowed = ("G4Exception : Analysis_W001" in block
                   and "G4RootNtupleFileManager::SetNtupleMergingMode" in block
                   and "Merging ntuples is not applicable in sequential application." in block
                   and "Setting was ignored." in block and "just a warning message" in block)
        return "" if allowed else block
    checked_log = re.sub(r"[^\n]*G4Exception-START.*?[^\n]*G4Exception-END[^\n]*",
                         exception_filter, log, flags=re.S)
    if re.search(r"G4Exception-START|COMMAND NOT FOUND|command refused|Illegal application state|Overlap is detected", checked_log, re.I):
        raise ValueError("Geant4 reported a command, geometry or runtime failure")
    if task["preset"] == "steel-module-stack-v2":
        if tool_source is None:
            raise ValueError("v2 audit requires the frozen audit implementation")
        code = ("import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                "from audit import audit_run; "
                "print(json.dumps(audit_run(Path(sys.argv[2]),json.loads(sys.argv[3]),int(sys.argv[4]),"
                "accounting_enabled=sys.argv[5]=='on',expected_seeds=(int(sys.argv[6]),int(sys.argv[7])))))")
        result = subprocess.run([str(PYTHON), "-B", "-c", code, str(tool_source), str(run_dir),
                                 json.dumps(task["config"]), str(task["events"]),
                                 "on" if task["accounting"] else "off", str(task["seed1"]), str(task["seed2"])],
                                text=True, capture_output=True)
        if result.returncode:
            raise ValueError("Frozen v2 audit failed: " + result.stderr.strip())
        report = json.loads(result.stdout)
    else:
        report = dict(passed=True, legacy_reference=True)
    report.update(event_count=task["events"], root_sha256=sha(root))
    return report


def artifact_hashes(run_dir):
    return {str(p.relative_to(run_dir)): sha(p) for p in Path(run_dir).rglob("*") if p.is_file()}


def verify_completed(task, batch=None):
    receipt = read(task["receipt"])
    if not receipt.get("accepted"):
        raise ValueError("Task is not accepted: " + task["task_id"])
    if not task.get("receipt_sha256") or sha(task["receipt"]) != task["receipt_sha256"]:
        raise ValueError("Accepted receipt changed: " + task["task_id"])
    if not receipt.get("audit_sha256") or sha(task["audit_path"]) != receipt["audit_sha256"]:
        raise ValueError("Accepted audit changed: " + task["task_id"])
    if (receipt.get("task_id") != task["task_id"] or receipt.get("events") != task["events"]
            or receipt.get("role") != task["role"]
            or Path(receipt.get("run_dir", "")).resolve() != Path(task["run_dir"]).resolve()):
        raise ValueError("Accepted receipt is bound to another task or run directory")
    if read(task["audit_path"]).get("passed") is not True:
        raise ValueError("Accepted audit no longer reports success")
    if batch is not None:
        binary = read(batch / task["role"] / "binary.json")
        if (receipt["executable_sha256"] != binary["sha256"]
                or receipt["manifest_sha256"] != sha(batch / "manifest.json")
                or receipt["source_manifest_sha256"] != sha(batch / task["role"] / "source-manifest.json")
                or receipt.get("registered_task_sha256") != registered_task_hash(batch, task["task_id"])):
            raise ValueError("Completed task belongs to another frozen build/batch")
        Path(task["run_dir"]).resolve().relative_to((batch / "runs" / task["task_id"]).resolve())
    for relative, expected in receipt["artifacts"].items():
        if sha(Path(task["run_dir"]) / relative) != expected:
            raise ValueError("Completed task artifact drift: " + task["task_id"] + "/" + relative)
    return receipt


def execute(batch, *, stage="all", role=None, task_id=None, retry_failed=False, allow_science=False):
    manifest, tasks = verify_registry(batch)
    verify_analysis_environment(batch)
    if manifest["matrix"] != "acceptance" and not allow_science:
        raise RuntimeError("Scientific tasks are only prepared; this command requires explicit --allow-science")
    if role != "reference":
        verify_controller_snapshot(batch)
    identity = environment_identity()
    by_id = {task["task_id"]: task for task in tasks}
    for task in tasks:
        if stage != "all" and task["stage"] != stage:
            continue
        if role and task["role"] != role or task_id and task["task_id"] != task_id:
            continue
        if task.get("accepted"):
            verify_completed(task, batch)
            continue
        parent = batch / "runs" / task["task_id"]
        attempts = sorted(parent.glob("attempt-*"))
        if attempts and not retry_failed:
            raise RuntimeError("Previous attempt exists; inspect it, then explicitly retry: " + task["task_id"])
        binary = verify_binary(batch, task["role"], identity)
        comparison = task.get("compare_to")
        if comparison and not by_id[comparison].get("accepted"):
            raise RuntimeError("Required comparison task is not accepted: " + comparison)
        if comparison:
            verify_completed(by_id[comparison], batch)
        attempt = parent / f"attempt-{len(attempts) + 1:03d}"
        attempt.mkdir(parents=True, exist_ok=False)
        pointer = attempt / "result.txt"
        env = dict(OPNOVICE2_EXECUTABLE=local.cpath(binary["path"]), G4RUN_MANAGER_TYPE="Serial", DISPLAY="",
                   UPDATE_LATEST="0", PLOT_WITH_ROOT="0", SCAN_RUNS_DIR=local.cpath(attempt),
                   SCAN_RESULT_POINTER=local.cpath(pointer), SCAN_RUN_ID_SUFFIX=task["task_id"],
                   SCAN_GIT_COMMIT=read(batch / task["role"] / "source-manifest.json")["git_commit"],
                   SCAN_GIT_BRANCH="frozen-stack-v2", SCAN_GIT_DIRTY="true", PYTHONDONTWRITEBYTECODE="1")
        receipt = dict(task_id=task["task_id"], role=task["role"], events=task["events"], accepted=False, started_utc=stamp(),
                       registered_task_sha256=registered_task_hash(batch, task["task_id"]),
                       controller_sha256=sha(__file__),
                       manifest_sha256=sha(batch / "manifest.json"), executable_sha256=binary["sha256"],
                       source_manifest_sha256=sha(batch / task["role"] / "source-manifest.json"),
                       image_id=identity["image_id"], purpose=manifest["purpose"])
        save(attempt / "receipt.json", receipt)
        print(f"START {task['task_id']} ({task['events']} events)", flush=True)
        journal(batch, "task-start", task_id=task["task_id"], attempt=attempt.name)
        start = time.monotonic()
        try:
            local.dexec(["timeout", "--signal=TERM", "--kill-after=30s", "900s", "bash", "./run_sipm_cavity_scan.sh",
                         *scan_args(task)], env=env, log=attempt / "launcher.log",
                        workdir=local.cpath(batch / task["role"] / "source/test/OpNovice2"))
            run_dir = REPO / Path(pointer.read_text().strip()).relative_to(local.CPROJECT)
            task["run_dir"] = str(run_dir)
            report = basic_audit(run_dir, task, batch / "candidate/source/tools/steel_stack_v2")
            if comparison:
                report["exact_comparison"] = compare_frozen(batch, by_id[comparison]["run_dir"], run_dir)
            if report.get("passed") is not True:
                raise ValueError("Audit did not confirm acceptance")
            save(attempt / "audit.json", report)
            receipt.update(accepted=True, finished_utc=stamp(), elapsed_seconds=time.monotonic() - start,
                           run_dir=str(run_dir), artifacts=artifact_hashes(run_dir),
                           audit_sha256=sha(attempt / "audit.json"))
            save(attempt / "receipt.json", receipt)
            task.update(accepted=True, receipt=str(attempt / "receipt.json"), audit_path=str(attempt / "audit.json"),
                        receipt_sha256=sha(attempt / "receipt.json"), accounting_enabled=task["accounting"])
            save(batch / "tasks.json", tasks)
            journal(batch, "task-accepted", task_id=task["task_id"], elapsed_seconds=receipt["elapsed_seconds"])
            print(f"PASS {task['task_id']} {receipt['elapsed_seconds']:.1f}s", flush=True)
        except Exception as exc:
            receipt.update(error=str(exc), finished_utc=stamp(), elapsed_seconds=time.monotonic() - start)
            save(attempt / "receipt.json", receipt)
            save(batch / "tasks.json", tasks)
            journal(batch, "task-failed", task_id=task["task_id"], error=str(exc))
            raise


def audit_existing(batch, task_id, reaudit_completed=False):
    """Re-audit a physically complete attempt; keep its original failed receipt."""
    _, tasks = verify_registry(batch)
    verify_analysis_environment(batch)
    task = next(t for t in tasks if t["task_id"] == task_id)
    if task.get("accepted") and not reaudit_completed:
        verify_completed(task, batch)
        return
    attempt = sorted((batch / "runs" / task_id).glob("attempt-*"))[-1]
    previous_path = Path(task["receipt"]) if task.get("accepted") else attempt / "receipt.json"
    previous = read(previous_path)
    binary = verify_binary(batch, task["role"])
    if previous["executable_sha256"] != binary["sha256"]:
        raise ValueError("Cannot re-audit a different executable")
    run_dir = REPO / Path((attempt / "result.txt").read_text().strip()).relative_to(local.CPROJECT)
    report = basic_audit(run_dir, task, batch / "candidate/source/tools/steel_stack_v2")
    if report.get("passed") is not True:
        raise ValueError("Audit did not confirm acceptance")
    if task.get("compare_to"):
        other = next(t for t in tasks if t["task_id"] == task["compare_to"])
        verify_completed(other, batch)
        report["exact_comparison"] = compare_frozen(batch, other["run_dir"], run_dir)
    number = len(list(attempt.glob("reaudit-[0-9][0-9][0-9].json"))) + 1
    report_path = attempt / f"reaudit-{number:03d}-checks.json"
    receipt_path = attempt / f"reaudit-{number:03d}.json"
    save(report_path, report)
    receipt = {**previous, "accepted": True, "reaudited_utc": stamp(), "run_dir": str(run_dir),
               "role": task["role"], "registered_task_sha256": registered_task_hash(batch, task_id),
               "audit_controller_sha256": sha(__file__),
               "previous_receipt": str(previous_path), "artifacts": artifact_hashes(run_dir),
               "audit_sha256": sha(report_path)}
    receipt.pop("error", None)
    save(receipt_path, receipt)
    task.update(accepted=True, run_dir=str(run_dir), receipt=str(receipt_path), audit_path=str(report_path),
                receipt_sha256=sha(receipt_path), accounting_enabled=task["accounting"])
    save(batch / "tasks.json", tasks)
    journal(batch, "existing-output-reaudited", task_id=task_id, new_simulation_events=0)
    print("REAUDIT PASS " + task_id)


def check(batch):
    manifest, tasks = verify_registry(batch)
    verify_analysis_environment(batch)
    accepted = [t for t in tasks if t.get("accepted")]
    for role in {t["role"] for t in accepted}:
        verify_source(batch / role)
        binary = read(batch / role / "binary.json")
        if sha(binary["path"]) != binary["sha256"]:
            raise RuntimeError("Frozen executable changed during acceptance")
    for task in accepted:
        verify_completed(task, batch)
    preserved = read(batch / "preserved-before.json") if (batch / "preserved-before.json").exists() else {}
    for name, before in preserved.items():
        path = REPO / name
        after = dict(exists=path.exists(), symlink=path.is_symlink())
        if path.is_symlink():
            after["target"] = str(path.readlink())
        elif path.is_file():
            after["sha256"] = sha(path)
        if before != after:
            raise RuntimeError("Protected legacy artifact changed: " + name)
    result = dict(all_passed=len(accepted) == len(tasks), tasks_accepted=len(accepted), tasks_total=len(tasks),
                  events_accepted=sum(t["events"] for t in accepted), events_registered=manifest["event_count"],
                  protected_artifacts_unchanged=len(preserved), purpose=manifest["purpose"],
                  no_scientific_conclusion=True)
    save(batch / "verification.json", result)
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=("snapshot", "build", "prepare", "run-local", "audit-existing", "check"))
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--role", choices=("reference", "candidate"))
    parser.add_argument("--matrix", choices=("acceptance", "sensitivity", "full"), default="acceptance")
    parser.add_argument("--events-per-task", type=int)
    parser.add_argument("--blocks", type=int)
    parser.add_argument("--campaign-seed", type=int)
    parser.add_argument("--total-event-budget", type=int)
    parser.add_argument("--stage", choices=("all", "science", *STAGES), default="all")
    parser.add_argument("--task-id")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--reaudit-completed", action="store_true")
    parser.add_argument("--allow-science", action="store_true")
    args = parser.parse_args()
    batch = checked_batch(args.batch_dir)
    if args.command in ("snapshot", "build"):
        if not args.role:
            parser.error("--role is required for snapshot/build")
        (snapshot if args.command == "snapshot" else build)(batch, args.role)
    elif args.command == "prepare":
        prepare(batch, args.matrix, events=args.events_per_task, blocks=args.blocks,
                campaign_seed=args.campaign_seed, budget=args.total_event_budget)
    elif args.command == "run-local":
        execute(batch, stage=args.stage, role=args.role, task_id=args.task_id,
                retry_failed=args.retry_failed, allow_science=args.allow_science)
    elif args.command == "audit-existing":
        if not args.task_id:
            parser.error("--task-id is required for audit-existing")
        audit_existing(batch, args.task_id, reaudit_completed=args.reaudit_completed)
    else:
        if not check(batch)["all_passed"]:
            return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
