#!/usr/bin/env python3
"""Freeze and check dimple geometry in separate zero-event Geant4 processes.

Uses only the Python standard library. No beamOn, Docker setup, or job submission.
Build the prepared source snapshot, then pass that executable to `run`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import resource
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = "404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5"
CASES = [
    ("flat-small", False, "opening", (2, 2, .5), False, -2.75, None),
    ("opening-small", True, "opening", (2, 2, .5), False, -2.25, None),
    ("surface-small", True, "surface", (2, 2, .5), False, -2.5 + math.sqrt(7) - .25 - 1e-6, None),
    ("flat-large", False, "external", (6, 6, .5), False, -2.75, None),
    ("external-large", True, "external", (6, 6, .5), False, -2.75, None),
    ("external-small", True, "external", (2, 2, .5), False, -2.75, None),
    ("reject-opening-large", True, "opening", (6, 6, .5), False, None, "OpNovice2_Dimple_008"),
    ("reject-surface-large", True, "surface", (6, 6, .5), False, None, "OpNovice2_Dimple_008"),
    ("reject-external-grease", True, "external", (6, 6, .5), True, None, "OpNovice2_Dimple_015"),
    ("reject-external-wide", True, "external", (102, 6, .5), False, None, "OpNovice2_Dimple_017"),
    ("reject-external-zero", True, "external", (0, 6, .5), False, None, "OpNovice2_Dimple_016"),
    ("reject-external-thick", True, "external", (6, 6, 500), False, None, "OpNovice2_Dimple_018"),
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True)


def prepare(batch: Path) -> None:
    batch.mkdir(parents=True, exist_ok=False)
    source = ROOT / "test/OpNovice2"
    paths = sorted([source / "CMakeLists.txt", source / "OpNovice2.cc",
                    *source.glob("src/*.cc"), *source.glob("include/*.hh"),
                    *source.glob("*.mac"), *source.glob("*.out")])
    hashes = {}
    for path in paths:
        relative = path.relative_to(ROOT)
        target = batch / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[str(relative)] = digest(target)
    shutil.copyfile(__file__, batch / "geometry_check.py")
    (batch / "source.patch").write_text(git("diff", "--binary", "HEAD"))
    manifest = {"schema": "dimple-geometry-check-v1", "base_commit": BASE,
                "head_commit": git("rev-parse", "HEAD").strip(),
                "git_status": git("status", "--porcelain"),
                "source_sha256": hashes,
                "checker_sha256": digest(batch / "geometry_check.py"),
                "events": 0, "cases": []}
    for index, (name, dimple, mode, size, grease, centre, rejection) in enumerate(CASES):
        directory = batch / "cases" / name
        directory.mkdir(parents=True)
        # Geometry-only initialization; material optical tables and particle source
        # are deliberately not promoted to a production optical protocol here.
        macro = f"""/control/verbose 0
/run/verbose 0
/tracking/verbose 0
/random/setSeeds {1907100001 + 2 * index} {1907100002 + 2 * index}
/opnovice2/boxMaterial G4_PLASTIC_SC_VINYLTOLUENE
/opnovice2/worldMaterial G4_AIR
/opnovice2/tank/size 100 100 5 mm
/opnovice2/tank/bottomCavity false
/opnovice2/stack/enabled false
/opnovice2/stack/layoutStudy false
/opnovice2/absorber/enabled false
/opnovice2/sipm/layout single
/opnovice2/sipm/face -Z
/opnovice2/sipm/localPosition 0 0 0 mm
/opnovice2/sipm/size {' '.join(str(x) for x in size)} mm
/opnovice2/grease/enabled {str(grease).lower()}
/opnovice2/surfaceModel unified
/opnovice2/surfaceType dielectric_dielectric
/opnovice2/surfaceFinish polished
/opnovice2/surfaceSigmaAlpha 0
/opnovice2/dimple/enabled {str(dimple).lower()}
/opnovice2/dimple/radius 3 mm
/opnovice2/dimple/mode hemisphere
/opnovice2/dimple/sipmMode {mode}
/run/initialize
/geometry/test/resolution 10000
/geometry/test/run
/control/echo DIMPLE_GEOMETRY_DONE
"""
        (directory / "run.mac").write_text(macro)
        manifest["cases"].append({"name": name, "dimple": dimple, "mode": mode,
                                  "size_mm": list(size), "centre_z_mm": centre,
                                  "expected_rejection": rejection,
                                  "macro_sha256": digest(directory / "run.mac")})
    save(batch / "manifest.json", manifest)
    print(f"Prepared {len(CASES)} zero-event cases in {batch}")


def run(batch: Path, executable: Path) -> None:
    manifest_path = batch / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (batch / "verification.json").exists():
        raise ValueError("Verification already exists; prepare a fresh batch")
    if digest(Path(__file__)) != manifest["checker_sha256"]:
        raise ValueError("Checker differs from the frozen preparation")
    for relative, expected in manifest["source_sha256"].items():
        if digest(batch / "source" / relative) != expected:
            raise ValueError(f"Frozen source changed: {relative}")
    for case in manifest["cases"]:
        directory = batch / "cases" / case["name"]
        if digest(directory / "run.mac") != case["macro_sha256"]:
            raise ValueError(f"Input changed: {case['name']}")
        if (directory / "simulation.log").exists():
            raise ValueError("An attempt already exists; prepare a fresh batch")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    results = []
    for case in manifest["cases"]:
        directory = batch / "cases" / case["name"]
        with (directory / "simulation.log").open("wb") as output:
            result = subprocess.run([str(executable), "run.mac"], cwd=directory,
                                    stdout=output, stderr=subprocess.STDOUT, timeout=120)
        log = (directory / "simulation.log").read_text(errors="replace")
        rejection = case["expected_rejection"]
        checks = {"macro_no_beam_on": "/run/beamOn" not in (directory / "run.mac").read_text()}
        if rejection:
            checks.update(rejected=result.returncode != 0,
                          expected_error=rejection in log,
                          stopped_before_completion="DIMPLE_GEOMETRY_DONE" not in log)
        else:
            positions = re.findall(r"SiPM placement:.*world=\(([^)]+)\) mm", log)
            sizes = re.findall(r"SiPM geometry: full_size=\(([^)]+)\) mm", log)
            actual = [float(x) for x in positions[0].split(",")] if positions else []
            actual_size = [float(x) for x in sizes[0].split(",")] if sizes else []
            expected = [0., 0., case["centre_z_mm"]]
            tank_shape = "G4SubtractionSolid" if case["dimple"] else "G4Box"
            checks.update(normal_exit=result.returncode == 0,
                          completed="DIMPLE_GEOMETRY_DONE" in log,
                          single_sensor=len(positions) == len(sizes) == 1,
                          sensor_position=len(actual) == 3 and all(
                              math.isclose(a, b, abs_tol=2e-6) for a, b in zip(actual, expected)),
                          sensor_size=len(actual_size) == 3 and all(
                              math.isclose(a, b, abs_tol=1e-9) for a, b in zip(actual_size, case["size_mm"])),
                          geometry_check_completed="Geometry overlaps check completed !" in log,
                          tank_overlap_check=f"Checking overlaps for volume Tank:0 ({tank_shape}) ... OK!" in log,
                          sensor_overlap_check="Checking overlaps for volume SiPM:0 (G4Box) ... OK!" in log,
                          no_overlap=not re.search(r"Overlap is detected|GeomVol1002|overlap with", log),
                          no_command_error=not re.search(r"COMMAND NOT FOUND|Illegal application state|parameter.*out of range", log),
                          no_exception="G4Exception" not in log)
            if case["dimple"]:
                checks["dimple_identity"] = (
                    f"Dimple geometry: shape=hemisphere, radius=3 mm, sipm_mode={case['mode']}, opening_z=-2.5 mm" in log)
        receipt = {"name": case["name"], "process_exit": result.returncode,
                   "checks": checks, "passed": all(checks.values()),
                   "macro_sha256": case["macro_sha256"],
                   "log_sha256": digest(directory / "simulation.log")}
        save(directory / "receipt.json", receipt)
        results.append(receipt)
        print(f"{case['name']}: {'PASS' if receipt['passed'] else 'FAIL'}", flush=True)
    verification = {"status": "passed" if all(x["passed"] for x in results) else "failed",
                    "scope": "Zero-event geometry only; no optical transport or scientific sample",
                    "events": 0, "manifest_sha256": digest(manifest_path),
                    "executable_sha256": digest(executable), "cases": results}
    save(batch / "verification.json", verification)
    if verification["status"] != "passed":
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.executable:
            parser.error("prepare does not take an executable")
        prepare(args.batch.resolve())
    else:
        if not args.executable:
            parser.error("run requires --executable from the frozen source build")
        run(args.batch.resolve(), args.executable.resolve())


if __name__ == "__main__":
    main()
