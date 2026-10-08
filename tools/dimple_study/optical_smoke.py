#!/usr/bin/env python3
"""Prepare and run a fixed local dimple optical engineering check (90 events).

30 executed regression events precede 60 independently seeded smoke events.
No scan-size adaptation, physics changes, retries, or cluster submission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_COMMIT = "52fc522fe1391e6ae0171d3e3f5c9a69b465b7cb"
TEMPLATE = "test/OpNovice2/ej200_sipm_gps_test.mac"
TOOLS = ["tools/dimple_study/optical_smoke.py",
         "tools/dimple_study/optical_audit.py", "tools/layout_study/audit.py"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def macro(template: str, case: dict) -> str:
    retained = []
    for line in template.splitlines():
        active = line.partition("#")[0].strip()
        command = active.split()[0] if active else ""
        if (command.startswith("/process/optical/") or command in {
            "/opnovice2/boxMaterial", "/opnovice2/worldMaterial",
            "/opnovice2/boxProperty", "/opnovice2/boxConstProperty",
            "/opnovice2/worldProperty", "/opnovice2/worldConstProperty",
            "/opnovice2/surfaceModel", "/opnovice2/surfaceType",
            "/opnovice2/surfaceFinish", "/opnovice2/surfaceSigmaAlpha"}):
            retained.append(active)
    material_and_physics = "\n".join(retained)
    if "/opnovice2/boxConstProperty SCINTILLATIONYIELD 10000" not in retained:
        raise ValueError("Unexpected nominal yield in the frozen template")
    observer = "/opnovice2/diagnostics/dimpleOptical true\n" if case["diagnostics"] else ""
    random_state = ("/opnovice2/diagnostics/saveRandomState true\n"
                    if case["variant"] != "reference" else "")
    return f"""# Fixed engineering inputs; legacy optical process settings are retained.
/control/verbose 0
/run/verbose 0
/tracking/verbose 0
/tracking/storeTrajectory 0
{material_and_physics}
/opnovice2/tank/size 100 100 5 mm
/opnovice2/tank/bottomCavity false
/opnovice2/absorber/enabled false
/opnovice2/stack/enabled false
/opnovice2/stack/layoutStudy false
/opnovice2/grease/enabled false
/opnovice2/sipm/layout single
/opnovice2/sipm/face -Z
/opnovice2/sipm/localPosition 0 0 0 mm
/opnovice2/sipm/size 6 6 0.5 mm
/opnovice2/dimple/enabled {str(case['geometry'] == 'dimple').lower()}
/opnovice2/dimple/radius 3 mm
/opnovice2/dimple/mode hemisphere
/opnovice2/dimple/sipmMode external
{observer}{random_state}/run/initialize
/geometry/test/resolution 10000
/geometry/test/run
/analysis/setFileName result
/analysis/h1/set 2 100 2.0 3.5
/analysis/h1/set 3 400 0 100
/analysis/h1/set 27 100 2.0 3.5
/analysis/h1/set 28 400 0 100
/opnovice2/gun/electronEnergyMode fixed
/gps/particle e-
/gps/pos/type Point
/gps/pos/centre {case['x_mm']} 0 4 mm
/gps/direction 0 0 -1
/gps/energy 1.0 MeV
# Reset after geometry sampling so geometry checks cannot shift event seeds.
/random/setSeeds {case['seeds'][0]} {case['seeds'][1]}
/run/printProgress 10
/run/beamOn {case['events']}
/control/echo DIMPLE_OPTICAL_DONE
"""


def prepare(batch: Path, reference: Path) -> None:
    baseline = json.loads(git("show", f"{REFERENCE_COMMIT}:tools/dimple_study/geometry-validation.json"))
    expected_reference = baseline["verification"]["executable_sha256"]
    if sha(reference) != expected_reference:
        raise ValueError("Reference executable does not match the accepted geometry build")
    for relative, expected in baseline["source_sha256"].items():
        if hashlib.sha256(git("show", f"{REFERENCE_COMMIT}:{relative}")).hexdigest() != expected:
            raise ValueError(f"Reference commit/source mismatch: {relative}")
    template_bytes = git("show", f"{REFERENCE_COMMIT}:{TEMPLATE}")
    batch.mkdir(parents=True, exist_ok=False)
    source = ROOT / "test/OpNovice2"
    files = sorted([source / "CMakeLists.txt", source / "OpNovice2.cc",
                    *source.glob("src/*.cc"), *source.glob("include/*.hh"),
                    *source.glob("*.mac"), *source.glob("*.out")])
    source_hashes = {}
    for path in files:
        relative = path.relative_to(ROOT)
        target = batch / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        source_hashes[str(relative)] = sha(target)
    tool_hashes = {}
    for relative in TOOLS:
        path = ROOT / relative
        target = batch / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        tool_hashes[relative] = sha(target)
    (batch / "source.patch").write_bytes(git("diff", "--binary", "HEAD"))
    (batch / "reference").mkdir()
    shutil.copy2(reference, batch / "reference/OpNovice2")
    (batch / "optical-template.mac").write_bytes(template_bytes)
    cases = []
    for g, geometry in enumerate(("flat", "dimple")):
        for variant in ("reference", "off", "on"):
            cases.append({"name": f"gate-{geometry}-{variant}", "phase": "gate",
                          "geometry": geometry, "variant": variant,
                          "diagnostics": variant == "on", "events": 5, "x_mm": 0,
                          "seeds": [1910080001 + 2*g, 1910080002 + 2*g]})
    for g, geometry in enumerate(("flat", "dimple")):
        for p, x in enumerate((0, 3.2, 10)):
            i = 3*g + p
            cases.append({"name": f"smoke-{geometry}-x{x}", "phase": "smoke",
                          "geometry": geometry, "variant": "on", "diagnostics": True,
                          "events": 10, "x_mm": x,
                          "seeds": [1910080101 + 2*i, 1910080102 + 2*i]})
    for case in cases:
        directory = batch / "cases" / case["name"]
        directory.mkdir(parents=True)
        (directory / "run.mac").write_text(macro(template_bytes.decode(), case))
        case["macro_sha256"] = sha(directory / "run.mac")
    manifest = {"schema": "dimple-optical-engineering-v1", "reference_commit": REFERENCE_COMMIT,
                "head_commit": git("rev-parse", "HEAD").decode().strip(),
                "git_status": git("status", "--porcelain").decode(),
                "reference_executable_sha256": expected_reference,
                "source_sha256": source_hashes, "tool_sha256": tool_hashes,
                "template": {"path": TEMPLATE, "commit": REFERENCE_COMMIT,
                             "sha256": sha(batch / "optical-template.mac")},
                "actual_event_budget": 90, "smoke_events": 60, "regression_events": 30,
                "scope": "Local engineering only; no layout ranking, grid scan or precision claim",
                "cases": cases}
    save(batch / "manifest.json", manifest)
    print(f"Prepared 30 regression + 60 smoke events in {batch}")


def verify_source(batch: Path, manifest: dict) -> None:
    for relative, expected in manifest["source_sha256"].items():
        if sha(batch / "source" / relative) != expected:
            raise ValueError(f"Frozen source changed: {relative}")


def build(batch: Path) -> None:
    manifest_path = batch / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    verify_source(batch, manifest)
    if (batch / "build").exists() or (batch / "build.log").exists():
        raise ValueError("Existing build attempt; prepare a fresh batch")
    version = subprocess.check_output(["geant4-config", "--version"], text=True).strip()
    if version != "11.4.2":
        raise ValueError("Geant4 11.4.2 required")
    commands = [["cmake", "-S", str(batch / "source/test/OpNovice2"),
                 "-B", str(batch / "build"), "-DCMAKE_BUILD_TYPE=Release", "-DWITH_GEANT4_UIVIS=OFF"],
                ["cmake", "--build", str(batch / "build"), "--parallel", "2"]]
    with (batch / "build.log").open("wb") as output:
        for command in commands:
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT)
            if result.returncode:
                save(batch / "STOP.json", {"phase": "build", "command": command,
                                           "exit_code": result.returncode})
                raise ValueError("Build failed; see build.log")
    verify_source(batch, manifest)
    save(batch / "build-receipt.json", {"manifest_sha256": sha(manifest_path),
         "commands": commands, "exit_code": 0, "geant4_version": version,
         "source_sha256": manifest["source_sha256"], "build_log_sha256": sha(batch / "build.log"),
         "executable_sha256": sha(batch / "build/OpNovice2")})
    print("Frozen-source build completed.", flush=True)


def run(batch: Path, candidate: Path) -> None:
    from optical_audit import audit_case, compare_legacy
    if os.environ.get("G4RUN_MANAGER_TYPE") != "Serial":
        raise ValueError("Set G4RUN_MANAGER_TYPE=Serial explicitly")
    if subprocess.check_output(["geant4-config", "--version"], text=True).strip() != "11.4.2":
        raise ValueError("Geant4 11.4.2 required")
    manifest_path = batch / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    verify_source(batch, manifest)
    build_receipt = json.loads((batch / "build-receipt.json").read_text())
    if (candidate != (batch / "build/OpNovice2").resolve() or
        build_receipt["manifest_sha256"] != sha(manifest_path) or
        build_receipt["source_sha256"] != manifest["source_sha256"] or
        build_receipt["build_log_sha256"] != sha(batch / "build.log") or
        build_receipt["executable_sha256"] != sha(candidate) or build_receipt["exit_code"] != 0):
        raise ValueError("Candidate does not match this batch's frozen-source build receipt")
    for relative, expected in manifest["tool_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise ValueError(f"Tool changed: {relative}")
    reference = batch / "reference/OpNovice2"
    if sha(reference) != manifest["reference_executable_sha256"]:
        raise ValueError("Frozen reference executable changed")
    for case in manifest["cases"]:
        directory = batch / "cases" / case["name"]
        if sha(directory / "run.mac") != case["macro_sha256"]:
            raise ValueError(f"Input changed: {case['name']}")
        if (directory / "receipt.json").exists() or (directory / "simulation.log").exists():
            raise ValueError("Existing attempt found; preserve it and prepare a fresh batch")
    identity = {"manifest_sha256": sha(manifest_path), "candidate_sha256": sha(candidate),
                "reference_sha256": sha(reference), "events_requested": 90}
    save(batch / "execution.json", identity)
    reports = []
    comparisons = []
    for phase in ("gate", "smoke"):
        for case in (c for c in manifest["cases"] if c["phase"] == phase):
            directory = batch / "cases" / case["name"]
            executable = reference if case["variant"] == "reference" else candidate
            expected_executable = (identity["reference_sha256"] if case["variant"] == "reference"
                                   else identity["candidate_sha256"])
            if sha(executable) != expected_executable:
                save(batch / "STOP.json", {"phase": phase, "case": case["name"],
                                           "reason": "Executable changed before execution"})
                raise ValueError("Executable changed before execution")
            started = time.monotonic()
            with (directory / "simulation.log").open("wb") as output:
                try:
                    result = subprocess.run([str(executable), "run.mac"], cwd=directory,
                                            stdout=output, stderr=subprocess.STDOUT, timeout=180)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = None
            receipt = {"case": case["name"], "exit_code": code,
                       "elapsed_seconds": time.monotonic() - started,
                       "executable_sha256": sha(executable),
                       "macro_sha256": case["macro_sha256"],
                       "log_sha256": sha(directory / "simulation.log")}
            save(directory / "receipt.json", receipt)
            try:
                if receipt["executable_sha256"] != expected_executable:
                    raise ValueError("Executable changed during execution")
                report = audit_case(directory, case, code)
            except Exception as error:
                save(batch / "STOP.json", {"phase": phase, "case": case["name"],
                                           "reason": str(error), "automatic_retry": False})
                raise
            save(directory / "audit.json", report)
            reports.append({"name": case["name"], **report})
            print(f"{case['name']}: PASS ({case['events']} events)", flush=True)
        if phase == "gate":
            try:
                for geometry in ("flat", "dimple"):
                    for left, right in (("reference", "off"), ("off", "on")):
                        result = compare_legacy(batch / "cases" / f"gate-{geometry}-{left}",
                                                batch / "cases" / f"gate-{geometry}-{right}",
                                                require_rng=left == "off")
                        comparisons.append({"geometry": geometry, "left": left, "right": right, **result})
            except Exception as error:
                save(batch / "STOP.json", {"phase": "noninterference", "reason": str(error),
                                           "automatic_retry": False})
                raise
            save(batch / "noninterference.json", {"status": "passed", "comparisons": comparisons})
            print("Reference/off/on noninterference passed; proceeding to 60-event smoke.", flush=True)
    save(batch / "verification.json", {"status": "passed", "scope": manifest["scope"],
                                       **identity, "reports": reports, "comparisons": comparisons})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build", "run"))
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.reference:
            parser.error("prepare requires --reference from geometry-v2")
        prepare(args.batch.resolve(), args.reference.resolve())
    elif args.action == "build":
        build(args.batch.resolve())
    else:
        if not args.candidate:
            parser.error("run requires --candidate from the frozen source build")
        run(args.batch.resolve(), args.candidate.resolve())


if __name__ == "__main__":
    main()
