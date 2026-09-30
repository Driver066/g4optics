#!/usr/bin/env python3
"""Audit a local environment batch; this program never runs Geant4.

Run with test/OpNovice2/.venv-analysis/bin/python. Inputs are batch/tasks.json,
batch/before/preserved-files.json, and batch/external_gui.json. Outputs go only
to batch/verification. --skip-gui produces a partial data report, never a full
environment acceptance. ROOT is independently exercised on every event file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone


REPO = Path(__file__).resolve().parents[2]
COUNTS = ("generated_optical_photons", "scintillation_photons",
          "sipm_detected_photons", "cerenkov_photons")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def resolve(path, base):
    path = Path(path)
    return path if path.is_absolute() else base / path


def one(paths, label):
    paths = sorted(paths)
    require(len(paths) == 1, f"Expected one {label}; found {len(paths)}")
    return paths[0]


def single_csv(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == 1, f"Expected one row in {path}")
    return rows[0]


def integer(value, label):
    number = float(value)
    require(math.isfinite(number) and number == int(number), f"Invalid integer: {label}")
    return int(number)


def verify_image(path):
    import matplotlib.image as mpimg
    import numpy as np
    require(path.is_file() and path.stat().st_size > 1000, f"Missing/empty plot: {path}")
    if path.suffix == ".pdf":
        with path.open("rb") as stream:
            require(stream.read(5) == b"%PDF-", f"Invalid PDF: {path}")
        return {"path": str(path), "bytes": path.stat().st_size}
    pixels = mpimg.imread(path)
    require(pixels.ndim == 3 and min(pixels.shape[:2]) >= 200, f"Invalid PNG size: {path}")
    rgb = pixels[:, :, :3]
    require(np.isfinite(rgb).all() and float(rgb.std()) > 0.01, f"Blank PNG: {path}")
    return {"path": str(path), "bytes": path.stat().st_size,
            "width": int(pixels.shape[1]), "height": int(pixels.shape[0]),
            "pixel_std": float(rgb.std()), "check": "decoded-and-nonblank"}


def check_log(path, events):
    content = path.read_text(encoding="utf-8", errors="replace")
    require(re.search(r"Geant4 version Name:.*(?:geant4-11-04-patch-02|11\.4\.2)", content),
            f"Geant4 11.4.2 banner missing: {path}")
    failures = []
    for line in content.splitlines():
        if re.search(r"\bERROR\b|\bFatal\w*\b|COMMAND\s+NOT\s+FOUND|command.*not found|"
                     r"Aborting execution|Segmentation (?:fault|violation)|GeomVol1002|"
                     r"Overlap (?:is )?detected|Overlap with volume|overlapping volumes", line,
                     flags=re.IGNORECASE):
            failures.append(line[:500])
    require(not failures, f"Simulation log errors in {path}: {failures}")
    reported = re.findall(r"Number of events:\s*(\d+)\b", content)
    require(reported and int(reported[-1]) == events, f"Completed event count missing: {path}")
    require(re.search(r"close file\s*:.*- done", content), f"ROOT file close marker missing: {path}")
    return {"path": str(path), "geant4_version": "11.4.2", "completed_events": events,
            "warning_lines": [line for line in content.splitlines()
                              if "warning" in line.lower()][:20]}


def check_root_render(root_file, output, root_command, expected_entries):
    prefix = output / "root_photon_histogram"
    # Remove only our previous receipt and render products, so a failed rerun
    # cannot be accepted because an earlier invocation left valid files behind.
    for suffix in (".json", ".png", ".pdf"):
        prefix.with_suffix(suffix).unlink(missing_ok=True)
    env = dict(os.environ, G4OPTICS_VERIFY_ROOT_INPUT=str(root_file),
               G4OPTICS_VERIFY_ROOT_OUTPUT=str(prefix))
    command = [root_command, "-l", "-b", "-q", str(Path(__file__).with_name("verify_environment_root.C"))]
    result = subprocess.run(command, cwd=output, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    (output / "root-render.log").write_text(result.stdout, encoding="utf-8")
    require(result.returncode == 0, f"ROOT rendering exited {result.returncode}")
    receipt = read_json(prefix.with_suffix(".json"))
    require(receipt.get("root_version") == "6.38.04", f"Unexpected ROOT version: {receipt}")
    for key in ("tree_entries", "drawn_entries", "histogram_entries"):
        require(receipt.get(key) == expected_entries, f"ROOT receipt {key} mismatch: {receipt}")
    receipt["images"] = [verify_image(prefix.with_suffix(suffix)) for suffix in (".png", ".pdf")]
    return receipt


def audit_task(task, batch, output, root_command):
    import numpy as np
    import uproot
    task_id = str(task["task_id"])
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id), "Unsafe task_id")
    run_dir = resolve(task["run_dir"], batch).resolve()
    events = integer(task["events"], "task events")
    require(events == 100, f"{task_id}: environment acceptance requires 100 events")
    root_file = one((run_dir / "outputs").glob("*.root"), "event ROOT file")
    summary_file = one((run_dir / "outputs").glob("*_summary.csv"), "summary CSV")
    macro_file = one((run_dir / "macros").glob("*.mac"), "simulation macro")
    log_file = one((run_dir / "logs").glob("*.log"), "simulation log")
    config = read_json(run_dir / "run_config.json")
    require(config["simulation"]["events_per_point"] == events, f"{task_id}: config events mismatch")
    dimple = config["dimple"]
    require(isinstance(dimple["enabled"], bool), f"{task_id}: invalid dimple enabled flag")
    if dimple["enabled"]:
        require(dimple["mode"] == "hemisphere" and dimple["sipm_mode"] == "opening" and
                float(dimple["radius_mm"]) == 3.0, f"{task_id}: expected W08 r3 opening geometry")
    for seed in ("seed1", "seed2"):
        require(config["random"][seed] == integer(task[seed], seed), f"{task_id}: config {seed} mismatch")
    summary = single_csv(summary_file)
    map_row = single_csv(run_dir / "efficiency_map.csv")
    with uproot.open(root_file) as root:
        require("scan" in root, f"{task_id}: scan tree missing")
        tree = root["scan"]
        require(int(tree.num_entries) == events, f"{task_id}: ROOT events mismatch")
        required = ["event_id", *COUNTS, "collection_efficiency", "collection_efficiency_valid",
                    "shoot_x_mm", "shoot_y_mm"]
        require(all(key in tree for key in required), f"{task_id}: required event fields missing")
        arrays = tree.arrays(required, library="np")
    ids = arrays["event_id"]
    require(np.array_equal(np.sort(ids), np.arange(events)), f"{task_id}: nonunique/noncontiguous event IDs")
    order = np.argsort(ids)
    sorted_counts = {}
    totals = {}
    for key in COUNTS:
        values = arrays[key]
        require(np.isfinite(values).all() and (values >= 0).all() and
                np.equal(values, np.floor(values)).all(), f"{task_id}: invalid {key}")
        totals[key] = int(values.sum())
        sorted_counts[key] = values[order]
        require(integer(summary[key], key) == totals[key], f"{task_id}: summary {key} disagrees with ROOT")
        # The runner's compatibility map retains the historical column set.
        # New event/summary fields remain mandatory in their authoritative files.
        if key != "cerenkov_photons" or key in map_row:
            require(integer(map_row[key], key) == totals[key], f"{task_id}: map {key} disagrees with ROOT")
    generated, scint, detected, cerenkov = (arrays[key] for key in COUNTS)
    require(totals[COUNTS[0]] > 0, f"{task_id}: no generated photons")
    require((detected <= generated).all(), f"{task_id}: detected exceeds generated")
    require((cerenkov == 0).all(), f"{task_id}: unexpected Cerenkov photons")
    require(np.array_equal(generated, scint), f"{task_id}: unexpected non-scintillation light")
    valid = generated > 0
    require(np.array_equal(arrays["collection_efficiency_valid"], valid.astype(int)),
            f"{task_id}: collection validity flags mismatch")
    require(np.allclose(arrays["collection_efficiency"][valid], detected[valid] / generated[valid],
                        rtol=1e-12, atol=1e-15), f"{task_id}: event collection ratio mismatch")
    require(np.isnan(arrays["collection_efficiency"][~valid]).all(), f"{task_id}: invalid CE must be NaN")
    ce = totals[COUNTS[2]] / totals[COUNTS[0]]
    require(integer(summary["committed_events"], "committed_events") == events,
            f"{task_id}: summary committed_events mismatch")
    require(integer(summary["collection_efficiency_valid"], "CE valid") == 1,
            f"{task_id}: summary collection must be valid")
    for row in (summary, map_row):
        require(integer(row["events"], "events") == events, f"{task_id}: events mismatch")
        require(math.isclose(float(row["collection_efficiency"]), ce, rel_tol=1e-12, abs_tol=1e-15),
                f"{task_id}: collection ratio mismatch")
    if "committed_events" in map_row:
        require(integer(map_row["committed_events"], "committed_events") == events,
                f"{task_id}: map committed_events mismatch")
    if "collection_efficiency_valid" in map_row:
        require(integer(map_row["collection_efficiency_valid"], "CE valid") == 1,
                f"{task_id}: map collection must be valid")
    for axis in ("x", "y"):
        require(np.allclose(arrays[f"shoot_{axis}_mm"], float(task[f"{axis}_mm"]),
                            rtol=0, atol=1e-9), f"{task_id}: source {axis} mismatch")
    macro = macro_file.read_text(encoding="utf-8")
    require(re.search(r"/random/setSeeds\s+" + str(int(task["seed1"])) + r"\s+" +
                      str(int(task["seed2"])) + r"\s*(?:\n|$)", macro), f"{task_id}: macro seeds missing")
    log = check_log(log_file, events)
    task_output = output / task_id
    task_output.mkdir(exist_ok=True)
    rendered = check_root_render(root_file, task_output, root_command, events)
    record = {"task_id": task_id, "geometry": task["geometry"], "x_mm": float(task["x_mm"]),
              "y_mm": float(task["y_mm"]), "seed1": int(task["seed1"]), "seed2": int(task["seed2"]),
              "events": events, **totals, "collection_efficiency": ce,
              "dimple_enabled": dimple["enabled"],
              "detected_per_event": totals[COUNTS[2]] / events, "root_path": str(root_file),
              "root_sha256": hashlib.sha256(root_file.read_bytes()).hexdigest(),
              "log_audit": log, "root_render": rendered, "passed": True}
    return record, sorted_counts


def check_preserved(batch):
    document = read_json(batch / "before" / "preserved-files.json")
    if isinstance(document, dict) and "files" in document:
        records = document["files"]
    elif isinstance(document, dict):
        records = [{"path": path, "sha256": value, "exists": value is not None}
                   for path, value in document.items()]
    else:
        records = document
    require(isinstance(records, list) and records, "Empty preserved-files inventory")
    checks = []
    for record in records:
        path = resolve(record["path"], REPO)
        expected_exists = record.get("exists", record.get("sha256") is not None)
        exists = path.exists() or path.is_symlink()
        require(exists == expected_exists, f"Preserved path existence changed: {path}")
        if exists:
            if record.get("symlink") and "target" in record:
                record = dict(record, symlink_target=record["target"])
            require(record.get("sha256") is not None or "symlink_target" in record,
                    f"Preservation record lacks hash or symlink target: {path}")
            if "symlink_target" in record:
                require(path.is_symlink() and os.readlink(path) == record["symlink_target"],
                        f"Preserved symlink changed: {path}")
            if record.get("sha256") is not None:
                require(path.is_file(), f"Preserved file is no longer a file: {path}")
                require(hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"],
                        f"Preserved file hash changed: {path}")
        checks.append({"path": str(path), "passed": True, "exists": exists})
    return checks


def python_plot(records, output):
    import matplotlib.pyplot as plt
    scalar_keys = [key for key in records[0] if key not in ("root_render", "log_audit", "passed")]
    with (output / "six_points.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=scalar_keys)
        writer.writeheader()
        writer.writerows({key: row[key] for key in scalar_keys} for row in records)
    figure, axis = plt.subplots(figsize=(10, 6), constrained_layout=True)
    for geometry in sorted(set(str(row["geometry"]) for row in records)):
        rows = sorted((row for row in records if row["geometry"] == geometry),
                      key=lambda row: (row["x_mm"], row["y_mm"]))
        labels = [f"({row['x_mm']:g}, {row['y_mm']:g})" for row in rows]
        horizontal = len({row["y_mm"] for row in rows}) == 1
        coordinates = [row["x_mm"] for row in rows] if horizontal else list(range(len(rows)))
        axis.plot(coordinates, [row["collection_efficiency"] * 100 for row in rows],
                  marker="o", label=geometry)
        axis.set_xticks(coordinates, labels)
    axis.set(xlabel="Source position (x, y), mm", ylabel="Collected / generated photons (%)",
             title="Environment acceptance: six source points")
    axis.text(0.02, 0.02, "100 events per point; environment check, not a physics result",
              transform=axis.transAxes, fontsize=9)
    axis.grid(alpha=0.2)
    axis.legend()
    path = output / "six_points.png"
    figure.savefig(path, dpi=160)
    plt.close(figure)
    return verify_image(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", required=True, type=Path)
    parser.add_argument("--root-command", default="root")
    parser.add_argument("--skip-gui", action="store_true")
    args = parser.parse_args()
    require(args.batch_dir.is_absolute(), "--batch-dir must be absolute")
    batch = args.batch_dir.resolve()
    output = batch / "verification"
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(output / "matplotlib-cache"))
    os.environ.setdefault("XDG_CACHE_HOME", str(output / "cache"))
    os.environ["MPLBACKEND"] = "Agg"
    report = {"schema_version": "g4optics-local-environment-verification-v1",
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "batch_dir": str(batch), "tasks": [], "repeats": [], "errors": [],
              "gui": {"status": "skipped" if args.skip_gui else "pending"},
              "all_passed": False, "data_passed": False}
    try:
        root_command = shutil.which(args.root_command)
        require(root_command is not None, f"ROOT command not available: {args.root_command}")
        tasks = read_json(batch / "tasks.json")
        require(isinstance(tasks, list), "tasks.json must be a list")
        ids = [str(task["task_id"]) for task in tasks]
        require(len(ids) == len(set(ids)), "Duplicate task IDs")
        baseline = [task for task in tasks if not task.get("repeat_of")]
        repeats = [task for task in tasks if task.get("repeat_of")]
        require(len(baseline) == 6 and len(repeats) >= 1, "Expected six source points and a repeat")
        geometries = {str(task["geometry"]) for task in baseline}
        require(len(geometries) == 2, "Expected two geometries")
        positions = [{(float(task["x_mm"]), float(task["y_mm"])) for task in baseline
                      if str(task["geometry"]) == geometry} for geometry in geometries]
        require(len(positions[0]) == 3 and positions[0] == positions[1], "Expected three matched positions")
        require(len({str(resolve(task["run_dir"], batch).resolve()) for task in tasks}) == len(tasks),
                "Tasks must use distinct run directories")
        arrays = {}
        records = {}
        for task in tasks:
            try:
                record, counts = audit_task(task, batch, output, root_command)
                records[str(task["task_id"])] = record
                arrays[str(task["task_id"])] = counts
                report["tasks"].append(record)
            except Exception as error:
                report["errors"].append(f"{task.get('task_id')}: {type(error).__name__}: {error}")
        import numpy as np
        by_id = {str(task["task_id"]): task for task in tasks}
        for task in repeats:
            current, reference = str(task["task_id"]), str(task["repeat_of"])
            require(reference in by_id and not by_id[reference].get("repeat_of"), "Invalid repeat reference")
            for key in ("geometry", "x_mm", "y_mm", "seed1", "seed2", "events"):
                require(task[key] == by_id[reference][key], f"Repeat {current} changes {key}")
            require(current in arrays and reference in arrays, f"Repeat {current} lacks audited event data")
            matches = {key: bool(np.array_equal(arrays[current][key], arrays[reference][key])) for key in COUNTS}
            report["repeats"].append({"task_id": current, "repeat_of": reference,
                                      "event_count_fields_equal": matches, "passed": all(matches.values())})
            require(all(matches.values()), f"Fixed-seed event counts differ: {current}")
        if all(str(task["task_id"]) in records for task in baseline):
            geometry_flags = [{records[str(task["task_id"])]["dimple_enabled"] for task in baseline
                               if str(task["geometry"]) == geometry} for geometry in geometries]
            require(all(len(flags) == 1 for flags in geometry_flags) and
                    set.union(*geometry_flags) == {False, True},
                    "The two geometry labels must represent flat and dimple runs")
            report["python_plot"] = python_plot([records[str(task["task_id"])] for task in baseline], output)
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    try:
        report["preserved_files"] = check_preserved(batch)
    except Exception as error:
        report["errors"].append(f"Preservation: {type(error).__name__}: {error}")
    report["data_passed"] = not report["errors"]
    if not args.skip_gui:
        try:
            gui = read_json(batch / "external_gui.json")
            require(gui.get("passed") is True and bool(gui.get("evidence")), "GUI acceptance evidence missing")
            report["gui"] = {"status": "passed", "external_evidence": gui}
        except Exception as error:
            report["gui"] = {"status": "failed"}
            report["errors"].append(f"GUI: {type(error).__name__}: {error}")
    report["all_passed"] = report["data_passed"] and report["gui"]["status"] == "passed"
    target = output / "verification.json"
    target.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(target), "all_passed": report["all_passed"],
                      "data_passed": report["data_passed"], "gui": report["gui"]["status"],
                      "errors": report["errors"]}, indent=2))
    return 0 if (report["data_passed"] if args.skip_gui else report["all_passed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
