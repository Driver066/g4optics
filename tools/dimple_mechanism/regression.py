#!/usr/bin/env python3
"""Frozen 30-event regression against the accepted 5 mm dimple executable."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ENTRY = "tools/dimple_mechanism/regression.py"
TOOLS = (ENTRY, "tools/dimple_study/optical_smoke.py",
         "tools/dimple_study/optical_audit.py", "tools/layout_study/audit.py")
TEMPLATE = "test/OpNovice2/ej200_sipm_gps_test.mac"
BASELINE = "tools/dimple_study/optical-validation.json"
sys.path.insert(0, str(ROOT / "tools/dimple_study"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("x") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def prepare(output, engineering, reference):
    from optical_smoke import macro
    require(not output.exists(), "Use a new regression output directory")
    baseline = json.loads((ROOT / BASELINE).read_text())
    require(sha(reference) == baseline["candidate_executable_sha256"],
            "Reference is not the accepted dimple optical executable")
    candidate = engineering / "build/OpNovice2"
    receipt_path = engineering / "build-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    manifest_path = engineering / "manifest.json"
    require(receipt["exit_code"] == 0 and receipt["geant4_version"] == "11.4.2",
            "Engineering build did not pass")
    require(sha(candidate) == receipt["executable_sha256"], "Candidate binary changed")
    require(sha(manifest_path) == receipt["manifest_sha256"], "Engineering manifest changed")
    engineering_manifest = json.loads(manifest_path.read_text())
    template_path = engineering / "optical-template.mac"
    require(sha(template_path) == engineering_manifest["template_sha256"],
            "Frozen optical template changed")
    require(sha(engineering / "build.log") == receipt["build_log_sha256"], "Build log changed")
    for relative, expected in receipt["source_sha256"].items():
        require(sha(engineering / "source" / relative) == expected, f"Build source changed: {relative}")
    output.mkdir(parents=True)
    (output / "bin").mkdir()
    shutil.copy2(candidate, output / "bin/candidate")
    shutil.copy2(reference, output / "bin/reference")
    shutil.copyfile(receipt_path, output / "build-receipt.json")
    shutil.copyfile(manifest_path, output / "engineering-manifest.json")
    shutil.copyfile(ROOT / BASELINE, output / "baseline-validation.json")
    shutil.copyfile(template_path, output / "optical-template.mac")
    runtime = {}
    for relative in TOOLS:
        target = output / "runtime" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        runtime[relative] = sha(target)
    cases = []
    template = (output / "optical-template.mac").read_text()
    for g, geometry in enumerate(("flat", "dimple")):
        for variant in ("reference", "off", "on"):
            case = {"name": f"{geometry}-{variant}", "geometry": geometry,
                    "variant": "on" if variant == "on" else "off",
                    "diagnostics": variant == "on", "events": 5, "x_mm": 0,
                    "require_response": False, "seeds": [1926102701+2*g, 1926102702+2*g],
                    "executable": "reference" if variant == "reference" else "candidate",
                    "comparison_variant": variant}
            text = macro(template, case)
            if variant == "on":
                text = text.replace("/run/initialize\n",
                                    "/opnovice2/diagnostics/issue9Geometry true\n/run/initialize\n")
            directory = output / "cases" / case["name"]
            directory.mkdir(parents=True)
            (directory / "run.mac").write_text(text)
            case["macro_sha256"] = sha(directory / "run.mac")
            cases.append(case)
    manifest = {
        "schema": "issue9-legacy-regression-v1", "events": 30, "cases": cases,
        "reference_executable_sha256": sha(output / "bin/reference"),
        "candidate_executable_sha256": sha(output / "bin/candidate"),
        "build_receipt_sha256": sha(output / "build-receipt.json"),
        "engineering_manifest_sha256": sha(output / "engineering-manifest.json"),
        "baseline_validation_sha256": sha(output / "baseline-validation.json"),
        "template_sha256": sha(output / "optical-template.mac"),
        "runtime_sha256": runtime, "dataset_manifest_sha256": baseline["dataset_manifest_sha256"],
        "scope": "Two old 5 mm geometries, matched seeds, 30 engineering events; no science scan",
    }
    save(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha(output / "manifest.json") + "\n")
    print("Prepared 6 regression tasks / 30 events", flush=True)


def verify(output):
    require(Path(__file__).resolve() == (output / "runtime" / ENTRY).resolve(),
            "Execute the frozen runtime copy")
    require(sha(output / "manifest.json") == (output / "manifest.sha256").read_text().strip(),
            "Regression manifest changed")
    manifest = json.loads((output / "manifest.json").read_text())
    for relative, expected in manifest["runtime_sha256"].items():
        require(sha(output / "runtime" / relative) == expected, f"Runtime changed: {relative}")
    files = {"bin/reference": "reference_executable_sha256",
             "bin/candidate": "candidate_executable_sha256", "build-receipt.json": "build_receipt_sha256",
             "engineering-manifest.json": "engineering_manifest_sha256",
             "baseline-validation.json": "baseline_validation_sha256", "optical-template.mac": "template_sha256"}
    for path, key in files.items():
        require(sha(output / path) == manifest[key], f"Frozen input changed: {path}")
    for case in manifest["cases"]:
        require(sha(output / "cases" / case["name"] / "run.mac") == case["macro_sha256"],
                f"Macro changed: {case['name']}")
    return manifest


def run(output):
    from optical_audit import audit_case, compare_legacy
    manifest = verify(output)
    require(os.environ.get("G4RUN_MANAGER_TYPE") == "Serial", "Set G4RUN_MANAGER_TYPE=Serial")
    require(subprocess.check_output(["geant4-config", "--version"], text=True).strip() == "11.4.2",
            "Geant4 11.4.2 required")
    require(sha(Path(os.environ.get("ISSUE9_DATASET_MANIFEST", "/g4data/layout-study-SHA256SUMS"))) ==
            manifest["dataset_manifest_sha256"], "Dataset inventory differs from baseline")
    require(not any((output / name).exists() for name in ("attempt.json", "STOP.json", "verification.json")),
            "Regression attempt exists; retain it and prepare a new directory")
    save(output / "attempt.json", {"manifest_sha256": sha(output / "manifest.json"), "events": 30})
    reports = []
    try:
        for case in manifest["cases"]:
            verify(output)
            directory = output / "cases" / case["name"]
            with (directory / "simulation.log").open("xb") as log:
                result = subprocess.run([str(output / "bin" / case["executable"]), "run.mac"],
                                        cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            report = audit_case(directory, case, result.returncode)
            save(directory / "audit.json", report)
            reports.append({"name": case["name"], **report})
            print(f"{case['name']}: PASS", flush=True)
        comparisons = []
        for geometry in ("flat", "dimple"):
            for left, right in (("reference", "off"), ("off", "on")):
                comparison = compare_legacy(output / "cases" / f"{geometry}-{left}",
                                            output / "cases" / f"{geometry}-{right}", require_rng=True)
                comparisons.append({"geometry": geometry, "left": left, "right": right, **comparison})
        verify(output)
        save(output / "verification.json", {
            "status": "passed", "events": 30, "manifest_sha256": sha(output / "manifest.json"),
            "cases": reports, "comparisons": comparisons,
            "scope": manifest["scope"],
        })
        print("All 4 legacy ROOT/CSV/RNG comparisons passed", flush=True)
    except BaseException as error:
        save(output / "STOP.json", {"status": "failed", "reason": str(error), "automatic_retry": False})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engineering", type=Path)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.engineering is None or args.reference is None:
            parser.error("prepare requires --engineering and --reference")
        prepare(args.output.resolve(), args.engineering.resolve(), args.reference.resolve())
    else:
        if args.engineering is not None or args.reference is not None:
            parser.error("run uses only frozen inputs")
        run(args.output.resolve())


if __name__ == "__main__":
    main()
