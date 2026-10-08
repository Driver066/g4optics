#!/usr/bin/env python3
"""Independently check the accepted line-analysis numbers without resampling.

Reads all original ROOT events and the saved bootstrap arrays. Does not import
the production analyzer, simulate events, draw random numbers, or change inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
from datetime import datetime, timezone

import numpy as np
import uproot


X_VALUES = (-40., -10., -5., -3.2, -2.8, -1.5, 0., 1.5, 2.8, 3.2, 5., 10., 40.)
GEOMETRIES = ("flat", "dimple")
MEANS = ("mean_D", "mean_G", "mean_optical_parent_births", "mean_all_births",
         "mean_electron_edep_mev", "mean_tile_edep_mev")
RATIOS = ("legacy_D_over_G", "entry_per_all_births", "optical_parent_fraction")
RELATIVE = {"relative_D": "mean_D", "relative_G": "mean_G",
            "relative_legacy_ratio": "legacy_D_over_G",
            "relative_electron_edep": "mean_electron_edep_mev"}
CSV_EVENT_FIELDS = ("D", "G", "optical_parent_births", "all_births",
                    "electron_edep_mev", "tile_edep_mev")


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def read_csv(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def array_table(tree):
    columns = tree.arrays(library="np")
    if isinstance(columns, dict):
        return columns
    return {name: columns[name] for name in columns.dtype.names}


def safe_divide(a, b):
    a, b = np.broadcast_arrays(np.asarray(a, dtype=float), np.asarray(b, dtype=float))
    result = np.full(a.shape, np.nan)
    np.divide(a, b, out=result, where=b != 0)
    return result


class Checks:
    def __init__(self):
        self.count = 0
        self.numeric_values = 0
        self.max_absolute_difference = 0.

    def require(self, condition, label):
        self.count += 1
        if not bool(condition):
            raise ValueError(label)

    def numeric(self, actual, expected, label):
        a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        self.require(a.shape == b.shape, label + ": shape mismatch")
        self.require(np.allclose(a, b, rtol=1e-12, atol=1e-10, equal_nan=True), label)
        self.numeric_values += a.size
        valid = np.isfinite(a) & np.isfinite(b)
        if np.any(valid):
            self.max_absolute_difference = max(self.max_absolute_difference,
                                                float(np.max(np.abs(a[valid] - b[valid]))))

    def optional_number(self, actual, expected, label):
        if np.isfinite(expected):
            self.require(actual is not None, label + ": unexpected null")
            self.numeric(actual, expected, label)
        else:
            self.require(actual is None, label + ": expected null")

    def summary(self, published, point, draws, label, adjusted=False):
        self.optional_number(published["estimate"], point, label + ": point")
        invalid = int(np.count_nonzero(~np.isfinite(draws)))
        self.require(published["invalid_bootstrap"] == invalid, label + ": invalid count")
        endpoints = ("lo95", "hi95")
        probabilities = (.025, .975)
        if adjusted:
            endpoints += ("lo_adjusted", "hi_adjusted")
            probabilities += (.05 / 26., 1. - .05 / 26.)
        if invalid:
            self.require(all(published[key] is None for key in endpoints), label + ": invalid interval")
        else:
            quantiles = np.quantile(draws, probabilities, method="linear")
            self.numeric([published[key] for key in endpoints], quantiles, label + ": quantiles")


def check_analysis(batch, analysis, script, expected_manifest):
    checks = Checks()
    result = read_json(analysis / "result.json")
    manifest = read_json(batch / "manifest.json")
    final = read_json(batch / "verification.json")
    inventory = read_json(analysis / "input-inventory.json")
    checks.require(file_hash(batch / "manifest.json") == expected_manifest == result["source"]["manifest_sha256"],
                   "manifest identity")
    checks.require(file_hash(batch / "verification.json") == result["source"]["verification_sha256"],
                   "campaign verification identity")
    checks.require(file_hash(analysis / "bootstrap.npz") == result["bootstrap_sha256"], "NPZ checksum")
    checks.require(file_hash(analysis / "input-inventory.json") == result["input_inventory_sha256"],
                   "inventory checksum")
    checks.require(file_hash(script) == file_hash(analysis / "analyze_line.py") == result["analysis_script_sha256"],
                   "current/copy/reported analysis script identity")
    checks.require(final["status"] == "passed" and final["accepted_tasks"] == 52 and final["accepted_events"] == 5200,
                   "campaign completion")
    checks.require(file_hash(batch / "line-summary.csv") == final["line_summary_sha256"], "source CSV checksum")

    configs = {(row["geometry"], float(row["x_mm"])): row for row in result["configurations"]}
    source_summary = {(row["geometry"], float(row["x_mm"])): row for row in read_csv(batch / "line-summary.csv")}
    output_summary = {(row["geometry"], float(row["x_mm"])): row
                      for row in read_csv(analysis / "configuration-summary.csv")}
    expected_cells = {(g, x) for g in GEOMETRIES for x in X_VALUES}
    checks.require(len(result["configurations"]) == 26 and set(configs) == expected_cells,
                   "published cell matrix")
    checks.require(set(source_summary) == set(output_summary) == expected_cells, "CSV cell matrix")
    tasks = manifest["tasks"]
    expected_tasks = {(g, x, block) for g, x in expected_cells for block in (0, 1)}
    checks.require(len(tasks) == 52 and {(t["geometry"], float(t["x_mm"]), t["block"]) for t in tasks} == expected_tasks,
                   "52-task input matrix")
    task_names = {task["name"] for task in tasks}
    checks.require(len(task_names) == 52 and {p.name for p in (batch / "tasks").iterdir() if p.is_dir()} == task_names,
                   "task identifiers/directories")
    reports = {row["task"]: row for row in final["reports"]}
    inv = {row["task"]: row for row in inventory["tasks"]}
    checks.require(set(reports) == set(inv) == task_names, "accepted input inventory")
    exported = read_csv(analysis / "events.csv")
    csv_events = {(row["task"], int(row["event_id"])): row for row in exported}
    checks.require(len(exported) == len(csv_events) == 5200, "event CSV unique identity")
    raw = {}
    input_root_hashes = {}
    for task in tasks:
        name = task["name"]
        directory = batch / "tasks" / name
        audit = read_json(directory / "audit.json")
        receipt = read_json(directory / "receipt.json")
        checks.require(audit == reports[name] and audit["status"] == "passed" and receipt["exit_code"] == 0,
                       name + ": accepted receipt")
        for filename, key in (("result.root", "root_sha256"), ("result_summary.csv", "summary_sha256"),
                              ("simulation.log", "log_sha256"), ("result.rng", "rng_state_sha256")):
            digest = file_hash(directory / filename)
            checks.require(digest == audit[key] == inv[name]["hashes"][filename], name + ": " + filename)
            if filename == "result.root":
                input_root_hashes[name] = digest
        checks.require(file_hash(directory / "run.mac") == task["macro_sha256"] == receipt["macro_sha256"],
                       name + ": macro identity")
        with uproot.open(directory / "result.root") as root:
            scan = array_table(root["scan"])
            diagnostic = array_table(root["dimple_optical_event_v1"])
        for label, columns in (("scan", scan), ("diagnostic", diagnostic)):
            ids = columns["event_id"]
            checks.require(ids.dtype.kind in "iu" and len(ids) == 100 and set(map(int, ids)) == set(range(100)),
                           name + ": " + label + " event identity")
            order = np.argsort(ids)
            for key in columns:
                columns[key] = columns[key][order]
        values = np.column_stack([scan["sipm_detected_photons"], scan["generated_optical_photons"],
                                  diagnostic["optical_parent_scintillation_births"], diagnostic["all_optical_births"],
                                  scan["electron_tile_edep_mev"], scan["tile_edep_mev"]]).astype(float)
        checks.require(values.shape == (100, 6) and np.all(np.isfinite(values)) and np.all(values >= 0),
                       name + ": finite nonnegative raw data")
        checks.numeric(values[:, 1] + values[:, 2], values[:, 3], name + ": optical birth partition")
        checks.numeric(values[:, 0].sum(), audit["detected_photons"], name + ": accepted D sum")
        checks.numeric(values[:, 1].sum(), audit["generated_photons"], name + ": accepted G sum")
        checks.numeric(values[:, 5].sum(), audit["tile_edep_sum_mev"], name + ": accepted tile energy sum")
        key = (task["geometry"], float(task["x_mm"]), task["block"])
        raw[key] = values
        for event_id, numbers in enumerate(values):
            csv_row = csv_events[(name, event_id)]
            checks.require((csv_row["geometry"], float(csv_row["x_mm"]), int(csv_row["block"])) == key,
                           name + ": exported event cell")
            checks.numeric([float(csv_row[field]) for field in CSV_EVENT_FIELDS], numbers,
                           name + ": exported event values")

    checks.numeric(result["method"]["adjusted_confidence"], 1. - .05 / 13., "adjusted coverage")
    checks.numeric(result["method"]["adjusted_quantiles"], [.05 / 26., 1. - .05 / 26.], "adjusted probabilities")
    checks.require(result["method"]["bootstrap_replicates"] == 100000 and
                   result["method"]["block_bootstrap_replicates"] == 20000, "saved draw counts")
    point, draws_by_cell, expected_npz_keys = {}, {}, set()
    flags = {"blocks_pointwise95_exclude_zero": [], "symmetry_pointwise95_exclude_zero": [],
             "primary_adjusted_intervals_above_zero": [], "primary_adjusted_intervals_below_zero": [],
             "primary_adjusted_intervals_include_zero": [], "invalid_intervals": []}
    with np.load(analysis / "bootstrap.npz", allow_pickle=False) as archive:
        for geometry, x in sorted(expected_cells):
            cell = configs[(geometry, x)]
            key = cell["key"]
            values = np.concatenate([raw[(geometry, x, block)] for block in (0, 1)])
            numbers = dict(zip(MEANS, values.sum(axis=0) / 200.))
            numbers.update(legacy_D_over_G=safe_divide(values[:, 0].sum(), values[:, 1].sum()),
                           entry_per_all_births=safe_divide(values[:, 0].sum(), values[:, 3].sum()),
                           optical_parent_fraction=safe_divide(values[:, 2].sum(), values[:, 3].sum()))
            point[(geometry, x)] = numbers
            cell_draws = {}
            for metric in (*MEANS, *RATIOS):
                npz_key = key + "__" + metric
                expected_npz_keys.add(npz_key)
                draws = archive[npz_key]
                checks.require(draws.shape == (100000,), npz_key + ": draw count")
                checks.summary(cell["metrics"][metric], numbers[metric], draws, npz_key)
                row = output_summary[(geometry, x)]
                for field in ("estimate", "lo95", "hi95"):
                    published = cell["metrics"][metric][field]
                    actual = row[metric + "_" + field]
                    if published is None:
                        checks.require(actual == "", npz_key + ": CSV undefined value")
                    else:
                        checks.numeric(float(actual), published, npz_key + ": CSV " + field)
                if cell["metrics"][metric]["invalid_bootstrap"]:
                    flags["invalid_intervals"].append(npz_key)
                cell_draws[metric] = draws
            for metric, numerator, denominator in (("legacy_D_over_G", "mean_D", "mean_G"),
                                                    ("entry_per_all_births", "mean_D", "mean_all_births"),
                                                    ("optical_parent_fraction", "mean_optical_parent_births", "mean_all_births")):
                checks.numeric(cell_draws[metric], safe_divide(cell_draws[numerator], cell_draws[denominator]),
                               key + ": saved replicate ratio " + metric)
            checks.numeric(cell_draws["mean_G"] + cell_draws["mean_optical_parent_births"],
                           cell_draws["mean_all_births"], key + ": saved birth partition")
            for index, metric in enumerate(MEANS):
                checks.require(np.all(cell_draws[metric] >= values[:, index].min() - 1e-10) and
                               np.all(cell_draws[metric] <= values[:, index].max() + 1e-10),
                               key + ": mean draws within raw-event bounds")
            draws_by_cell[(geometry, x)] = cell_draws
            source = source_summary[(geometry, x)]
            checks.numeric([float(source["mean_D"]), float(source["mean_G"]),
                            float(source["legacy_ratio_of_totals_D_over_G"]), float(source["optical_parent_births"])],
                           [numbers["mean_D"], numbers["mean_G"], numbers["legacy_D_over_G"], values[:, 2].sum()],
                           key + ": original source summary")
            block_means = []
            for block in (0, 1):
                bv = raw[(geometry, x, block)]
                means = bv.mean(axis=0)
                bm = cell["blocks"][block]
                checks.require(bm["block"] == block and bm["n"] == 100, key + ": block identity")
                checks.numeric([bm["mean_D"], bm["mean_G"], bm["legacy_D_over_G"]],
                               [means[0], means[1], safe_divide(bv[:, 0].sum(), bv[:, 1].sum())], key + ": block means")
                checks.numeric(float(source[f"block_{block}_mean_D"]), means[0], key + ": source block mean")
                block_means.append(means[0])
            block_key = key + "__block_difference_D"
            expected_npz_keys.add(block_key)
            block_draws = archive[block_key]
            checks.require(block_draws.shape == (20000,), key + ": block draw count")
            checks.summary(cell["block_difference_D"], block_means[1] - block_means[0], block_draws, block_key)
            bc = cell["block_difference_D"]
            if bc["lo95"] is not None and (bc["lo95"] > 0 or bc["hi95"] < 0):
                flags["blocks_pointwise95_exclude_zero"].append({"geometry": geometry, "x_mm": x, **bc})
            d, g = values[:, 0], values[:, 1]
            diagnostics = cell["diagnostics"]
            for metric, value in {"D_sd": d.std(ddof=1), "zero_D_fraction": np.mean(d == 0),
                                  "zero_G_fraction": np.mean(g == 0), "D_median": np.median(d),
                                  "D_p90": np.quantile(d, .9), "D_p99": np.quantile(d, .99), "D_max": d.max(),
                                  "largest_event_fraction_of_sum_D": safe_divide(d.max(), d.sum()),
                                  "top_one_percent_fraction_of_sum_D": safe_divide(np.sort(d)[-2:].sum(), d.sum())}.items():
                checks.optional_number(diagnostics[metric], value, key + ": distribution diagnostic " + metric)

        contrasts = {float(row["x_mm"]): row for row in result["contrasts"]}
        contrast_csv = {float(row["x_mm"]): row for row in read_csv(analysis / "contrasts.csv")}
        checks.require(len(result["contrasts"]) == 13 and set(contrasts) == set(contrast_csv) == set(X_VALUES),
                       "13 primary contrast cells")
        for position, x in enumerate(X_VALUES):
            f, d = point[("flat", x)], point[("dimple", x)]
            fb, db = draws_by_cell[("flat", x)], draws_by_cell[("dimple", x)]
            estimates = {"delta_D": d["mean_D"] - f["mean_D"]}
            samples = {"delta_D": db["mean_D"] - fb["mean_D"]}
            for metric, mean_metric in RELATIVE.items():
                estimates[metric] = safe_divide(d[mean_metric], f[mean_metric]) - 1.
                samples[metric] = safe_divide(db[mean_metric], fb[mean_metric]) - 1.
            for metric in estimates:
                npz_key = f"contrast_p{position:02d}__{metric}"
                expected_npz_keys.add(npz_key)
                checks.numeric(archive[npz_key], samples[metric], npz_key + ": independent-cell arithmetic")
                published = contrasts[x]["metrics"][metric]
                checks.summary(published, estimates[metric], samples[metric], npz_key, metric == "delta_D")
                for field, value in published.items():
                    text = contrast_csv[x][metric + "_" + field]
                    if value is None:
                        checks.require(text == "", npz_key + ": null contrast CSV")
                    else:
                        checks.numeric(float(text), value, npz_key + ": contrast CSV " + field)
            checks.numeric(1. + estimates["relative_D"],
                           (1. + estimates["relative_G"]) * (1. + estimates["relative_legacy_ratio"]),
                           f"x={x}: point response decomposition")
            checks.numeric(1. + samples["relative_D"],
                           (1. + samples["relative_G"]) * (1. + samples["relative_legacy_ratio"]),
                           f"x={x}: replicate response decomposition")
            primary = contrasts[x]["metrics"]["delta_D"]
            label = ("above_zero" if primary["lo_adjusted"] > 0 else
                     "below_zero" if primary["hi_adjusted"] < 0 else "include_zero")
            flags["primary_adjusted_intervals_" + label].append(x)

        checks.require(len(result["symmetry"]) == 12, "12 symmetry diagnostics")
        for row in result["symmetry"]:
            geometry, x = row["geometry"], float(row["abs_x_mm"])
            expected = point[(geometry, x)]["mean_D"] - point[(geometry, -x)]["mean_D"]
            delta = draws_by_cell[(geometry, x)]["mean_D"] - draws_by_cell[(geometry, -x)]["mean_D"]
            published = row["positive_minus_negative_D"]
            checks.summary(published, expected, delta, f"{geometry}/{x}: symmetry")
            if published["lo95"] is not None and (published["lo95"] > 0 or published["hi95"] < 0):
                flags["symmetry_pointwise95_exclude_zero"].append({"geometry": geometry, "abs_x_mm": x, **published})
        checks.require(set(archive.files) == expected_npz_keys, "exact NPZ array inventory")

    return {"schema": "dimple-line-independent-validation-v1", "status": "passed",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "52 original ROOT inputs, exported events and raw point estimates; existing NPZ arithmetic and percentile intervals; no production analyzer import, no resampling or simulation",
            "limits": ["Saved draws are not regenerated; the random sampling algorithm and weights are not independently reproduced.",
                       "No physics-validity or exact bootstrap-coverage claim follows from these numerical checks."],
            "tasks_checked": 52, "events_checked": 5200, "cells_checked": 26,
            "primary_contrasts_checked": 13, "block_diagnostics_checked": 26, "symmetry_diagnostics_checked": 12,
            "checks": checks.count, "numeric_values_compared": checks.numeric_values,
            "rtol": 1e-12, "atol": 1e-10, "max_absolute_difference": checks.max_absolute_difference,
            "manifest_sha256": expected_manifest, "result_sha256": file_hash(analysis / "result.json"),
            "bootstrap_sha256": file_hash(analysis / "bootstrap.npz"),
            "current_and_copied_analysis_script_sha256": file_hash(script),
            "validator_sha256": file_hash(Path(__file__)), "input_root_sha256": input_root_hashes,
            "software": {"python": platform.python_version(), "numpy": np.__version__, "uproot": uproot.__version__},
            "diagnostic_flags": flags}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--analysis-script", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Validation output already exists; use a new output path")
    report = check_analysis(args.batch.resolve(), args.analysis.resolve(), args.analysis_script.resolve(),
                            args.manifest_sha256)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "checks", "numeric_values_compared",
                     "max_absolute_difference", "diagnostic_flags")}, indent=2))


if __name__ == "__main__":
    main()
