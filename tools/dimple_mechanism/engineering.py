#!/usr/bin/env python3
"""Freeze, build and run issue #9 engineering checks only (zero + 42 events).

No production, cluster submission, retries, event extensions or overwrite path.
Run the copied source/tools/dimple_mechanism/engineering.py after preparation.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import time

import model

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_COMMIT = "5d6c50d37e33178e27710df5cc13e0127593acbe"
SCHEMA = "issue9-dimple-engineering-v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: dict) -> None:
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def prepare(batch: Path, image_id: str, dataset_manifest: Path) -> None:
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        raise ValueError("A full immutable Docker image SHA256 identity is required")
    if not dataset_manifest.is_file():
        raise ValueError("A verified dataset checksum inventory is required")
    reference_template = git("show", f"{REFERENCE_COMMIT}:{model.TEMPLATE}")
    if (ROOT/model.TEMPLATE).read_bytes() != reference_template:
        raise ValueError("Optical template differs from the accepted historical source")
    model.material_commands(reference_template.decode())
    registered = model.cases()
    # Validate every macro before creating a partial batch.
    macros = {case["name"]: model.macro(reference_template.decode(), case) for case in registered}
    app = ROOT/"test/OpNovice2"
    files = sorted([app/"CMakeLists.txt", app/"OpNovice2.cc", *app.glob("src/*.cc"),
                    *app.glob("include/*.hh"), *app.glob("*.mac"), *app.glob("*.out")])
    tool_files = sorted((ROOT/"tools/dimple_mechanism").rglob("*.py"))
    tool_files += [ROOT/"tools/dimple_study/optical_audit.py", ROOT/"tools/layout_study/audit.py"]
    batch.mkdir(parents=True, exist_ok=False)
    source_hashes, tool_hashes = {}, {}
    for paths, target_map in ((files, source_hashes), (tool_files, tool_hashes)):
        for path in paths:
            relative = path.relative_to(ROOT)
            target = batch/"source"/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            target_map[str(relative)] = sha(target)
    (batch/"source.patch").write_bytes(git("diff", "--binary", "HEAD"))
    (batch/"optical-template.mac").write_bytes(reference_template)
    shutil.copyfile(dataset_manifest, batch/"dataset-SHA256SUMS")
    for case in registered:
        directory = batch/"cases"/case["name"]
        directory.mkdir(parents=True)
        (directory/"run.mac").write_text(macros[case["name"]])
        case["macro_sha256"] = sha(directory/"run.mac")
    manifest = dict(schema=SCHEMA, head_commit=git("rev-parse", "HEAD").decode().strip(),
                    git_status=git("status", "--porcelain").decode(),
                    reference_template_commit=REFERENCE_COMMIT,
                    source_sha256=source_hashes, tool_sha256=tool_hashes,
                    template_sha256=sha(batch/"optical-template.mac"),
                    source_patch_sha256=sha(batch/"source.patch"),
                    image_id=image_id, dataset_manifest_sha256=sha(batch/"dataset-SHA256SUMS"),
                    configurations=list(model.CONFIGURATIONS), cases=registered,
                    total_smoke_events=42, geometry_events=0, source_z_mm=model.SOURCE_Z_MM,
                    scope="Fixed engineering validation only; production is unsupported")
    save(batch/"manifest.json", manifest)
    (batch/"manifest.sha256").write_text(sha(batch/"manifest.json")+"\n")
    print(f"Prepared {len(registered)} cases, 42 smoke events; manifest {sha(batch/'manifest.json')}", flush=True)


def verify_prepared(batch: Path, verify_runtime: bool = True) -> dict:
    manifest = read(batch/"manifest.json")
    if sha(batch/"manifest.json") != (batch/"manifest.sha256").read_text().strip():
        raise ValueError("Prepared manifest changed")
    if manifest["schema"] != SCHEMA or manifest["configurations"] != list(model.CONFIGURATIONS):
        raise ValueError("Wrong frozen model identity")
    expected_cases = model.cases()
    actual_cases = [{key: value for key, value in case.items() if key != "macro_sha256"} for case in manifest["cases"]]
    if actual_cases != expected_cases or manifest["total_smoke_events"] != 42:
        raise ValueError("Fixed engineering cases/event budget changed")
    inventory = {**manifest["source_sha256"], **manifest["tool_sha256"]}
    observed_files = {str(path.relative_to(batch/"source")) for path in (batch/"source").rglob("*") if path.is_file()}
    if observed_files != set(inventory):
        raise ValueError("Frozen source inventory changed")
    for relative, expected in inventory.items():
        if sha(batch/"source"/relative) != expected:
            raise ValueError(f"Frozen source/tool changed: {relative}")
    if verify_runtime:
        for relative, expected in manifest["tool_sha256"].items():
            if sha(ROOT/relative) != expected:
                raise ValueError(f"Running tool differs from frozen tool: {relative}")
    for name, key in (("optical-template.mac", "template_sha256"), ("dataset-SHA256SUMS", "dataset_manifest_sha256"),
                      ("source.patch", "source_patch_sha256")):
        if sha(batch/name) != manifest[key]:
            raise ValueError(f"Frozen input changed: {name}")
    template = (batch/"optical-template.mac").read_text()
    for case in manifest["cases"]:
        macro_path = batch/"cases"/case["name"]/"run.mac"
        if sha(macro_path) != case["macro_sha256"] or macro_path.read_text() != model.macro(template, case):
            raise ValueError(f"Frozen macro changed: {case['name']}")
    return manifest


def environment(manifest: dict) -> dict:
    if os.environ.get("G4RUN_MANAGER_TYPE") != "Serial":
        raise ValueError("Set G4RUN_MANAGER_TYPE=Serial explicitly")
    version = subprocess.check_output(["geant4-config", "--version"], text=True).strip()
    if version != "11.4.2":
        raise ValueError("Geant4 11.4.2 required")
    if os.environ.get("ISSUE9_IMAGE_ID") != manifest["image_id"]:
        raise ValueError("Set ISSUE9_IMAGE_ID to the immutable image used for this batch")
    inventory = Path(os.environ.get("ISSUE9_DATASET_MANIFEST", "/g4data/layout-study-SHA256SUMS"))
    if not inventory.is_file() or sha(inventory) != manifest["dataset_manifest_sha256"]:
        raise ValueError("Runtime dataset inventory differs from preparation")
    import numpy, uproot
    return dict(geant4_version=version, image_id=manifest["image_id"],
                dataset_manifest_sha256=sha(inventory), run_manager="Serial", python=platform.python_version(),
                platform=platform.platform(), machine=platform.machine(), numpy=numpy.__version__, uproot=uproot.__version__,
                dataset_validation_scope="Inventory identity only; independent full checksum receipt required")


def verify_build(batch: Path, manifest: dict) -> dict:
    receipt = read(batch/"build-receipt.json")
    if (receipt["manifest_sha256"] != sha(batch/"manifest.json") or
        receipt["source_sha256"] != manifest["source_sha256"] or receipt["exit_code"] != 0 or
        receipt["build_log_sha256"] != sha(batch/"build.log") or
        receipt["executable_sha256"] != sha(batch/"build/OpNovice2")):
        raise ValueError("Build does not match its frozen-source receipt")
    return receipt


def build(batch: Path, jobs: int) -> None:
    manifest = verify_prepared(batch)
    runtime = environment(manifest)
    if jobs < 1 or (batch/"build").exists() or (batch/"build.log").exists():
        raise ValueError("Invalid build jobs or existing build attempt; prepare a fresh batch")
    commands = [["cmake", "-S", str(batch/"source/test/OpNovice2"), "-B", str(batch/"build"),
                 "-DCMAKE_BUILD_TYPE=Release", "-DWITH_GEANT4_UIVIS=OFF"],
                ["cmake", "--build", str(batch/"build"), "--parallel", str(jobs)]]
    with (batch/"build.log").open("xb") as output:
        for command in commands:
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT)
            if result.returncode:
                save(batch/"STOP.json", dict(stage="build", command=command, exit_code=result.returncode))
                raise ValueError("Build failed; preserve batch and inspect build.log")
    verify_prepared(batch)
    save(batch/"build-receipt.json", dict(manifest_sha256=sha(batch/"manifest.json"), commands=commands,
         exit_code=0, geant4_version=runtime["geant4_version"], source_sha256=manifest["source_sha256"],
         build_log_sha256=sha(batch/"build.log"), executable_sha256=sha(batch/"build/OpNovice2"), environment=runtime))
    print("Frozen source build passed", flush=True)


def audit_geometry_case(directory: Path, case: dict, process_exit: int) -> dict:
    import audit
    log = (directory/"simulation.log").read_text(errors="replace")
    if "/run/beamOn" in (directory/"run.mac").read_text():
        raise ValueError("Geometry case contains particle events")
    rejection = case["expected_rejection"]
    if rejection:
        if process_exit == 0 or rejection not in log or "ISSUE9_GEOMETRY_DONE" in log:
            raise ValueError("Negative geometry case did not reject with its expected error")
        return dict(status="passed", expected_rejection=rejection, process_exit=process_exit)
    if process_exit != 0:
        raise ValueError(f"Valid geometry process exited {process_exit}")
    audit.legacy.audit_log(directory/"simulation.log")
    if len(__import__("re").findall(r"^ISSUE9_GEOMETRY_DONE\s*$", log, __import__("re").M)) != 1:
        raise ValueError("Missing geometry completion")
    return audit.audit_geometry(log, case["config"])


def execute_case(batch: Path, case: dict, executable: Path, timeout: int = 180) -> dict:
    """One immutable attempt; reusable by independent engineering orchestration."""
    directory = batch/"cases"/case["name"]
    if {path.name for path in directory.iterdir()} != {"run.mac"}:
        raise ValueError(f"Existing or contaminated case attempt: {case['name']}")
    started = time.monotonic()
    with (directory/"simulation.log").open("xb") as output:
        result = subprocess.run([str(executable), "run.mac"], cwd=directory,
                                stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
    if case["stage"] == "geometry":
        report = audit_geometry_case(directory, case, result.returncode)
    else:
        import audit
        report = audit.audit_case(directory, case, result.returncode)
    receipt = dict(name=case["name"], stage=case["stage"], events=case["events"], passed=True,
                   process_exit=result.returncode, elapsed_seconds=time.monotonic()-started,
                   macro_sha256=sha(directory/"run.mac"), log_sha256=sha(directory/"simulation.log"), audit=report)
    save(directory/"receipt.json", receipt)
    return receipt


def run(batch: Path, stage: str) -> None:
    if stage not in ("geometry", "smoke"):
        raise ValueError("Production execution is unsupported")
    manifest = verify_prepared(batch)
    runtime = environment(manifest)
    receipt = verify_build(batch, manifest)
    if (batch/"STOP.json").exists() or (batch/f"{stage}-execution.json").exists():
        raise ValueError("Existing/failed attempt; preserve it and prepare a fresh batch")
    if stage == "smoke":
        gate = read(batch/"geometry-verification.json")
        if gate["status"] != "passed" or gate["manifest_sha256"] != sha(batch/"manifest.json") or gate["executable_sha256"] != receipt["executable_sha256"]:
            raise ValueError("This exact binary and manifest need a passed geometry gate")
        for item in gate["cases"]:
            directory = batch/"cases"/item["name"]
            if sha(directory/"simulation.log") != item["log_sha256"] or read(directory/"receipt.json") != item:
                raise ValueError("Geometry gate evidence changed")
    selected = [case for case in manifest["cases"] if case["stage"] == stage]
    for case in selected:
        if {path.name for path in (batch/"cases"/case["name"]).iterdir()} != {"run.mac"}:
            raise ValueError(f"Existing/contaminated case attempt: {case['name']}")
    identity = dict(manifest_sha256=sha(batch/"manifest.json"), executable_sha256=receipt["executable_sha256"],
                    stage=stage, events=sum(case["events"] for case in selected), environment=runtime)
    save(batch/f"{stage}-execution.json", identity)
    resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    results = []
    try:
        for case in selected:
            verify_prepared(batch)
            verify_build(batch, manifest)
            result = execute_case(batch, case, batch/"build/OpNovice2")
            verify_prepared(batch)
            verify_build(batch, manifest)
            results.append(result)
            print(f"{case['name']}: PASS", flush=True)
    except Exception as exc:
        save(batch/"STOP.json", dict(stage=stage, case=case["name"], error=f"{type(exc).__name__}: {exc}", accepted_cases=len(results)))
        raise
    if stage == "smoke" and (sum(item["audit"]["generated_photons"] for item in results) == 0 or
                              sum(item["audit"]["detected_photons"] for item in results) == 0):
        save(batch/"STOP.json", dict(stage=stage, error="Entire smoke sample has no generated or collected response"))
        raise ValueError("Smoke did not exercise optical response")
    save(batch/f"{stage}-verification.json", dict(**identity, status="passed", cases=results,
         scope="Fixed engineering checks only; no production results or model ranking"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build", "run", "verify"))
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--image-id")
    parser.add_argument("--dataset-manifest", type=Path)
    parser.add_argument("--stage", choices=("geometry", "smoke"))
    parser.add_argument("--build-jobs", type=int, default=2)
    args = parser.parse_args()
    batch = args.batch.resolve()
    if args.action == "prepare":
        if not args.image_id or not args.dataset_manifest:
            parser.error("prepare requires --image-id and --dataset-manifest")
        prepare(batch, args.image_id, args.dataset_manifest.resolve())
        return
    if args.image_id or args.dataset_manifest:
        parser.error("image and dataset identities are frozen by prepare")
    if args.action == "run" and not args.stage:
        parser.error("run requires --stage")
    if args.action != "run" and args.stage:
        parser.error("--stage applies only to run")
    with (batch/".engineering.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "build":
            build(batch, args.build_jobs)
        elif args.action == "run":
            run(batch, args.stage)
        else:
            verify_prepared(batch)
            print("Frozen preparation verified")


if __name__ == "__main__":
    main()
