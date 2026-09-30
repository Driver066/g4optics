"""Finite engineering acceptance matrix and exact legacy-output comparisons."""
from __future__ import annotations

import hashlib
import csv
from pathlib import Path

import numpy as np
import uproot

LAYOUTS = ("back-four", "back-two", "edge-two", "back-center")
THICKNESSES = (4, 8, 12, 16, 20, 24)
GAPS = (0.5, 1.0)
STAGES = ("compatibility", "smoke", "noninterference", "repeat")


def seeds(key: str) -> tuple[int, int]:
    digest = hashlib.sha256(("steel-stack-v2-acceptance-20260929:" + key).encode()).digest()
    return tuple(1 + int.from_bytes(digest[i:i + 8], "big") % 2147483398 for i in (0, 8))


def config_id(layout, thickness, gap):
    return f"{layout}-t{thickness:02d}-g{int(gap * 1000):04d}"


def registered_tasks():
    tasks = []

    def add(task_id, stage, layout, thickness, gap, events, *, role="candidate",
            accounting=True, seed_key=None, compare_to=None, preset="steel-module-stack-v2"):
        tasks.append(dict(task_id=task_id, logical_task_id=task_id, stage=stage,
                          purpose="engineering-acceptance-not-scientific-evidence",
                          role=role, layout=layout, tile_thickness_mm=thickness,
                          readout_gap_mm=gap, events=events, seed_block=0,
                          seed1=seeds(seed_key or task_id)[0],
                          seed2=seeds(seed_key or task_id)[1],
                          accounting=accounting, preset=preset,
                          compare_to=compare_to))

    legacy = [("stack-v1-t04", "steel-module-stack-v1", "edge-two", 4),
              ("stack-v1-t24", "steel-module-stack-v1", "edge-two", 24),
              ("single-center", "steel-module-scan-v1", "back-center", 4),
              ("single-four", "steel-module-scan-v1", "back-four", 4)]
    for name, preset, layout, thickness in legacy:
        for role in ("reference", "candidate"):
            add(f"compat-{name}-{role}", "compatibility", layout, thickness, 0, 2,
                role=role, accounting=False, seed_key=f"compat-{name}", preset=preset,
                compare_to=f"compat-{name}-reference" if role == "candidate" else None)
    for layout in LAYOUTS:
        for thickness in THICKNESSES:
            for gap in GAPS:
                name = config_id(layout, thickness, gap)
                add(f"smoke-{name}", "smoke", layout, thickness, gap, 1)
    for layout in LAYOUTS:
        for thickness in (4, 24):
            for gap in GAPS:
                name = config_id(layout, thickness, gap)
                for enabled in (False, True):
                    suffix = "on" if enabled else "off"
                    add(f"observer-{name}-{suffix}", "noninterference", layout, thickness,
                        gap, 2, accounting=enabled, seed_key=f"observer-{name}",
                        compare_to=f"observer-{name}-off" if enabled else None)
    name = config_id("back-four", 24, 1.0)
    add(f"repeat-{name}", "repeat", "back-four", 24, 1.0, 2,
        seed_key=f"observer-{name}", compare_to=f"observer-{name}-on")
    assert len(tasks) == 89 and sum(t["events"] for t in tasks) == 130
    independent = {}
    for task in tasks:
        pair = task["seed1"], task["seed2"]
        if pair in independent and task["compare_to"] != independent[pair]:
            # The final repeat points to the on member of an explicit off/on pair.
            assert task["stage"] == "repeat"
        else:
            independent[pair] = task["task_id"]
    return tasks


def scan_args(task):
    args = ["full", "custom", "--study-preset", task["preset"],
            "--tile-thickness-mm", str(task["tile_thickness_mm"]),
            "--x-min", "0", "--x-max", "0", "--y-min", "0", "--y-max", "0",
            "--step", "1", "--grid-unit", "mm", "--events", str(task["events"]),
            "--seed1", str(task["seed1"]), "--seed2", str(task["seed2"]), "--no-root-plots"]
    if task["preset"] == "steel-module-stack-v2":
        args += ["--sipm-layout", task["layout"], "--readout-gap-mm", str(task["readout_gap_mm"]),
                 "--stack-photon-accounting", "on" if task["accounting"] else "off"]
        numerics = task.get("config", {}).get("optical_numerics", {"profile":"legacy","scale":0})
        args += ["--optical-numerics", numerics["profile"]]
        if numerics["scale"]: args += ["--optical-corner-scale",str(numerics["scale"])]
    elif task["preset"] == "steel-module-scan-v1":
        args += ["--sipm-layout", task["layout"], "--absorber-transverse-mm", "500"]
    return args


def single_file(run_dir, pattern):
    paths = list(Path(run_dir).glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"Expected one {pattern} in {run_dir}; found {len(paths)}")
    return paths[0]


def _equal(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and a.dtype == b.dtype and np.array_equal(
        a, b, equal_nan=a.dtype.kind in "fc")


def compare_runs(left_dir, right_dir):
    """Compare physical output, never ROOT serialization timestamps/metadata."""
    left = single_file(left_dir, "outputs/*.root")
    right = single_file(right_dir, "outputs/*.root")
    comparisons = []
    with uproot.open(left) as a, uproot.open(right) as b:
        def legacy_names(root):
            return {name: root[name].classname for name in root.keys(cycle=False)
                    if not name.startswith(("stack_event_v2", "stack_layer_v2", "stack_sensor_v2",
                                            "stack_photon_flow_v2", "w08_"))}
        if legacy_names(a) != legacy_names(b):
            raise ValueError("Legacy ROOT object names/classes differ")
        for name in legacy_names(a):
            oa, ob = a[name], b[name]
            if oa.classname == "TTree":
                if list(oa.keys()) != list(ob.keys()):
                    raise ValueError(f"Legacy branches differ: {name}")
                for field in oa.keys():
                    if not _equal(oa[field].array(library="np"), ob[field].array(library="np")):
                        raise ValueError(f"Legacy event values differ: {name}.{field}")
                comparisons.append(f"tree:{name}")
            elif oa.classname.startswith(("TH1", "TH2", "TH3")):
                if not _equal(oa.values(flow=True), ob.values(flow=True)):
                    raise ValueError(f"Histogram bins differ: {name}")
                if not _equal(oa.variances(flow=True), ob.variances(flow=True)):
                    raise ValueError(f"Histogram variances differ: {name}")
                for aa, ab in zip(oa.axes, ob.axes):
                    if not _equal(aa.edges(), ab.edges()):
                        raise ValueError(f"Histogram axis differs: {name}")
                for field in ("fEntries", "fTsumw", "fTsumw2", "fTsumwx", "fTsumwx2",
                              "fTsumwy", "fTsumwy2", "fTsumwxy"):
                    if field in oa.all_members and not _equal(oa.member(field), ob.member(field)):
                        raise ValueError(f"Histogram statistic differs: {name}.{field}")
                comparisons.append(f"histogram:{name}")
    rng_a = single_file(left_dir, "outputs/*.rng-end.txt")
    rng_b = single_file(right_dir, "outputs/*.rng-end.txt")
    if rng_a.read_bytes() != rng_b.read_bytes():
        raise ValueError("Run-end random engine states differ")
    summary_a = single_file(left_dir, "outputs/*_summary.csv")
    summary_b = single_file(right_dir, "outputs/*_summary.csv")
    with summary_a.open(newline="") as left_stream, summary_b.open(newline="") as right_stream:
        if list(csv.reader(left_stream)) != list(csv.reader(right_stream)):
            raise ValueError("Legacy summary header/data differ")
    return dict(passed=True, checks=comparisons + ["rng-end-exact", "summary-exact"],
                root_metadata_compared=False, left=str(left), right=str(right))
