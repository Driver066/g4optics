#!/usr/bin/env python3
"""Recompute the published dimple statistics from its public event CSV.

No Git checkout, original ROOT files, historical binary, or outputs directory is
required. This reuses the frozen analyzer's pure numerical helpers, so it is a
statistical replay, not a second independent raw-ROOT audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
import shutil

import numpy as np


HERE = Path(__file__).resolve().parent
HELPER_PATH = HERE / "analyze_line.py"
_SPEC = importlib.util.spec_from_file_location("_dimple_csv_numeric_helpers", HELPER_PATH)
core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(core)

POSITIONS = (-40., -10., -5., -3.2, -2.8, -1.5, 0., 1.5, 2.8, 3.2, 5., 10., 40.)
GEOMETRIES = ("flat", "dimple")
SEED = 2026100801
REPLICATES = 100000
BLOCK_REPLICATES = 20000
ATOL, RTOL = 1e-10, 1e-12
CSV_COLUMNS = ("task", "event_id", "geometry", "x_mm", "block", "D", "G",
               "optical_parent_births", "all_births", "electron_edep_mev", "tile_edep_mev")
COUNT_COLUMNS = ("D", "G", "optical_parent_births", "all_births")
VALUE_COLUMNS = (*COUNT_COLUMNS, "electron_edep_mev", "tile_edep_mev")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def integer(text, label):
    require(isinstance(text, str) and re.fullmatch(r"[+-]?\d+", text.strip()) is not None,
            f"{label}: expected an integer CSV value")
    return int(text)


def finite_float(text, label):
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: expected a numeric CSV value") from exc
    require(math.isfinite(number), f"{label}: nonfinite value")
    return number


def task_index(manifest):
    tasks = manifest["tasks"]
    expected = {(g, x, b) for g in GEOMETRIES for x in POSITIONS for b in (0, 1)}
    require(manifest["event_budget"] == 5200 and len(tasks) == 52, "Expected 52 tasks and 5200 events")
    require({(t["geometry"], t["x_mm"], t["block"]) for t in tasks} == expected,
            "Campaign is not the published 26-cell/two-block matrix")
    indexed = {t["name"]: t for t in tasks}
    require(len(indexed) == 52 and all(t["events"] == 100 for t in tasks), "Duplicate task or wrong event budget")
    seeds = [seed for t in tasks for seed in t["seeds"]]
    require(len(seeds) == len(set(seeds)) == 104 and
            all(type(seed) is int and 0 < seed < 2147483647 for seed in seeds), "Invalid/repeated simulation seeds")
    require(not set(seeds) & set(manifest["excluded_engineering_seed_values"]), "Engineering seeds reused")
    return indexed


def read_events(path, manifest):
    """Return all 52 ordered blocks, retaining zero observations and task IDs."""
    tasks = task_index(manifest)
    rows_by_task = {name: {} for name in tasks}
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames is not None and len(reader.fieldnames) == len(CSV_COLUMNS)
                and set(reader.fieldnames) == set(CSV_COLUMNS), "Unexpected/duplicate/missing event CSV columns")
        total = 0
        for row in reader:
            require(None not in row and all(value is not None for value in row.values()), "Malformed CSV row")
            name = row["task"]
            require(name in tasks, f"Unknown task {name}")
            task = tasks[name]
            event_id = integer(row["event_id"], name + "/event_id")
            block = integer(row["block"], name + "/block")
            x = finite_float(row["x_mm"], name + "/x_mm")
            require(0 <= event_id < 100 and event_id not in rows_by_task[name],
                    f"{name}: duplicate or out-of-range event_id")
            require((row["geometry"], x, block) == (task["geometry"], task["x_mm"], task["block"]),
                    f"{name}: event/campaign configuration mismatch")
            counts = [integer(row[column], name + "/" + column) for column in COUNT_COLUMNS]
            require(all(0 <= value <= 2**53 for value in counts), f"{name}: negative/nonexact count")
            require(counts[1] + counts[2] == counts[3] and counts[0] <= counts[3],
                    f"{name}: inconsistent exported birth/detection counts")
            energy = [finite_float(row[column], name + "/" + column) for column in VALUE_COLUMNS[4:]]
            require(all(value >= 0 for value in energy), f"{name}: negative deposited energy")
            rows_by_task[name][event_id] = counts + energy
            total += 1
    require(total == 5200, "Expected every one of the 5200 exported events")
    blocks = {}
    for name, task in tasks.items():
        records = rows_by_task[name]
        require(set(records) == set(range(100)), f"{name}: missing event IDs")
        key = (task["geometry"], float(task["x_mm"]), task["block"])
        blocks[key] = np.asarray([records[i] for i in range(100)], dtype=np.float64)
    return blocks


def load_bundle(bundle):
    index = json.loads((bundle / "artifact-index.json").read_text())
    inputs = {}
    for name in ("events.csv", "result.json", "campaign-manifest.json"):
        actual = digest(bundle / name)
        require(actual == index["files"][name], f"Published input checksum mismatch: {name}")
        inputs[name] = actual
    published = json.loads((bundle / "result.json").read_text())
    manifest = json.loads((bundle / "campaign-manifest.json").read_text())
    require(published["source"]["manifest_sha256"] == inputs["campaign-manifest.json"], "Wrong campaign manifest")
    require(digest(HELPER_PATH) == published["analysis_script_sha256"] == index["scripts"]["analyze_line.py"],
            "Numerical helpers differ from the published analysis")
    method = published["method"]
    require(method["bootstrap_seed"] == SEED and method["bootstrap_replicates"] == REPLICATES
            and method["block_bootstrap_replicates"] == BLOCK_REPLICATES, "Unexpected published bootstrap protocol")
    return published, manifest, read_events(bundle / "events.csv", manifest), inputs


def recompute(blocks):
    children = np.random.SeedSequence(SEED).spawn(52)
    configurations, saved, points, cell_draws = [], {}, {}, {}
    for gi, geometry in enumerate(GEOMETRIES):
        for pi, x in enumerate(POSITIONS):
            index, key = gi * 13 + pi, f"{geometry}_p{pi:02d}"
            pair = [blocks[(geometry, x, block)] for block in (0, 1)]
            values = np.vstack(pair)
            estimates = core.derived(values.mean(axis=0))
            draws = core.derived(core.bootstrap_means(values, np.random.default_rng(children[index]), REPLICATES))
            points[(geometry, x)], cell_draws[(geometry, x)] = estimates, draws
            for metric in core.METRICS:
                saved[f"{key}__{metric}"] = draws[metric]
            summaries = []
            for block, group in enumerate(pair):
                numbers = core.derived(group.mean(axis=0))
                summaries.append({"block": block, "n": 100, **{metric: core.finite_or_none(numbers[metric])
                    for metric in ("mean_D", "mean_G", "legacy_D_over_G")}})
            rng = np.random.default_rng(children[26 + index])
            difference = (core.bootstrap_means(pair[1][:, :1], rng, BLOCK_REPLICATES)[:, 0]
                          - core.bootstrap_means(pair[0][:, :1], rng, BLOCK_REPLICATES)[:, 0])
            saved[f"{key}__block_difference_D"] = difference
            configurations.append({"key": key, "geometry": geometry, "x_mm": x, "n": 200,
                "metrics": {metric: core.interval(estimates[metric], draws[metric]) for metric in core.METRICS},
                "blocks": summaries,
                "block_difference_D": core.interval(summaries[1]["mean_D"] - summaries[0]["mean_D"], difference)})
            print(f"Recomputed {geometry} x={x:g}: all 200 events", flush=True)
    contrasts = []
    for pi, x in enumerate(POSITIONS):
        f, d = points[("flat", x)], points[("dimple", x)]
        fb, db = cell_draws[("flat", x)], cell_draws[("dimple", x)]
        quantities = {"delta_D": (d["mean_D"] - f["mean_D"], db["mean_D"] - fb["mean_D"])}
        for name, metric in (("relative_D", "mean_D"), ("relative_G", "mean_G"),
                             ("relative_legacy_ratio", "legacy_D_over_G"),
                             ("relative_electron_edep", "mean_electron_edep_mev")):
            quantities[name] = (core.ratio(d[metric], f[metric]) - 1, core.ratio(db[metric], fb[metric]) - 1)
        for metric, (_, draws) in quantities.items():
            saved[f"contrast_p{pi:02d}__{metric}"] = draws
        contrasts.append({"x_mm": x, "metrics": {metric: core.interval(estimate, draws, metric == "delta_D")
            for metric, (estimate, draws) in quantities.items()}})
    symmetry = []
    for geometry in GEOMETRIES:
        for x in (1.5, 2.8, 3.2, 5., 10., 40.):
            difference = cell_draws[(geometry, x)]["mean_D"] - cell_draws[(geometry, -x)]["mean_D"]
            symmetry.append({"geometry": geometry, "abs_x_mm": x,
                "positive_minus_negative_D": core.interval(
                    points[(geometry, x)]["mean_D"] - points[(geometry, -x)]["mean_D"], difference)})
    return {"configurations": configurations, "contrasts": contrasts, "symmetry": symmetry}, saved


def compare_results(replayed, published):
    stats = {"numeric_values_checked": 0, "max_absolute_difference": 0.}

    def compare(actual, expected, label):
        if isinstance(actual, dict):
            require(isinstance(expected, dict) and set(actual) <= set(expected), label + ": missing published keys")
            for key, value in actual.items():
                compare(value, expected[key], label + "/" + key)
        elif isinstance(actual, list):
            require(isinstance(expected, list) and len(actual) == len(expected), label + ": list length mismatch")
            for i, (a, b) in enumerate(zip(actual, expected)):
                compare(a, b, f"{label}/{i}")
        elif isinstance(actual, (int, float)) and not isinstance(actual, bool):
            require(isinstance(expected, (int, float)) and not isinstance(expected, bool), label + ": numeric type")
            require(np.isclose(actual, expected, rtol=RTOL, atol=ATOL, equal_nan=False), label + ": numeric mismatch")
            stats["numeric_values_checked"] += 1
            stats["max_absolute_difference"] = max(stats["max_absolute_difference"], abs(float(actual) - expected))
        else:
            require(actual == expected, label + ": value mismatch")

    for section in ("configurations", "contrasts", "symmetry"):
        compare(replayed[section], published[section], section)
    return stats


def replay(bundle, output):
    require(not output.exists(), "Use a new output directory; existing artifacts will not be overwritten")
    published, manifest, blocks, input_hashes = load_bundle(bundle)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, output / "reanalyze_events.py")
    shutil.copy2(HELPER_PATH, output / "analyze_line.py")
    numerical, arrays = recompute(blocks)
    comparison = compare_results(numerical, published)
    np.savez_compressed(output / "bootstrap.npz", **arrays)
    numeric_result = {"schema": "dimple-public-csv-statistical-replay-v1",
        "scope": "Recomputed exported-event statistics only; no raw ROOT audit, simulation, hit-position or scintillation-centroid reconstruction",
        "source": {"input_sha256": input_hashes, "events": 5200, "tasks": 52, "cells": 26,
                   "simulation_commit": manifest["simulation_commit"]},
        "method": published["method"], **numerical}
    (output / "numeric-result.json").write_text(json.dumps(numeric_result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    report = {"schema": "dimple-public-csv-replay-report-v1", "status": "passed",
        "scope": numeric_result["scope"], "events_replayed": 5200, "cells_replayed": 26,
        "contrasts_replayed": 13, "block_diagnostics_replayed": 26, "symmetry_diagnostics_replayed": 12,
        "all_zero_response_events_retained": True, "atol": ATOL, "rtol": RTOL, **comparison,
        "input_sha256": input_hashes, "bootstrap_sha256": digest(output / "bootstrap.npz"),
        "numeric_result_sha256": digest(output / "numeric-result.json"),
        "program_sha256": digest(output / "reanalyze_events.py"),
        "numerical_helpers_sha256": digest(output / "analyze_line.py"),
        "software": {"python": platform.python_version(), "numpy": np.__version__},
        "limits": ["Reuses the frozen analyzer's numerical helpers; not an independent statistical implementation.",
                   "CSV has no hit-position or scintillation-centroid columns; those published spatial diagnostics are not copied or recomputed.",
                   "Replays published exported events; does not independently verify the original ROOT extraction.",
                   "NPZ compression and platform versions may change artifact bytes; numerical comparisons define acceptance."]}
    (output / "replay-report.json").write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "numeric_values_checked", "max_absolute_difference")}, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=HERE / "results/line-v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    replay(args.bundle.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
