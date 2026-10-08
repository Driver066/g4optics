#!/usr/bin/env python3
"""Render reviewed dimple line-scan estimates; never recompute statistics.

Input is analyze_line.py's JSON. Output is three PNG/SVG pairs. An existing
directory is allowed, but any existing target filename stops the whole export.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile

# Keep Matplotlib's cache out of the user's home directory, including imports.
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="dimple-mpl-"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np


GEOMETRIES = ("flat", "dimple")
STYLES = {
    "flat": {"color": "#2166ac", "marker": "o", "linestyle": "-", "label": "Flat"},
    "dimple": {"color": "#d95f02", "marker": "s", "linestyle": "--", "label": "Dimple"},
}
POSITIONS = (-40., -10., -5., -3.2, -2.8, -1.5, 0., 1.5, 2.8, 3.2, 5., 10., 40.)
NAMES = ("response", "contrasts", "diagnostics")
NEAR_LIMIT = 10.8
NEAR_TICKS = (-10, -5, -3, 0, 3, 5, 10)
NEUTRAL = "#3f4448"


def finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label}: expected a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label}: expected a finite number")
    return value


def interval(value: dict, label: str, adjusted: bool = False) -> None:
    finite(value["estimate"], f"{label}.estimate")
    pairs = [("lo95", "hi95")]
    if adjusted:
        pairs.append(("lo_adjusted", "hi_adjusted"))
    for lower, upper in pairs:
        lo = finite(value[lower], f"{label}.{lower}")
        hi = finite(value[upper], f"{label}.{upper}")
        if lo > hi:
            raise ValueError(f"{label}: reversed interval")


def validate(result: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    """Reject partial/mismatched fixed-design input rather than draw silent gaps."""
    groups: dict[str, list[dict]] = {geometry: [] for geometry in GEOMETRIES}
    for configuration in result["configurations"]:
        geometry = configuration["geometry"]
        if geometry not in groups:
            raise ValueError(f"Unexpected geometry: {geometry}")
        finite(configuration["x_mm"], "x_mm")
        if configuration["n"] != 200:
            raise ValueError("This fixed-design figure expects 200 events per geometry/point")
        for name in ("mean_D", "mean_G", "legacy_D_over_G", "mean_electron_edep_mev",
                     "optical_parent_fraction"):
            interval(configuration["metrics"][name], name)
        blocks = configuration["blocks"]
        if sorted(block["block"] for block in blocks) != [0, 1]:
            raise ValueError("Each configuration must retain both seed blocks 0 and 1")
        for block in blocks:
            if block["n"] != 100:
                raise ValueError("Each seed block must contain 100 events")
            finite(block["mean_D"], "block mean_D")
        groups[geometry].append(configuration)
    for geometry, rows in groups.items():
        rows.sort(key=lambda row: row["x_mm"])
        if tuple(row["x_mm"] for row in rows) != POSITIONS:
            raise ValueError(f"{geometry}: expected exactly the 13 reviewed numeric positions")
    contrasts = sorted(result["contrasts"], key=lambda row: row["x_mm"])
    if tuple(row["x_mm"] for row in contrasts) != POSITIONS:
        raise ValueError("Expected one contrast at each of the 13 positions")
    for row in contrasts:
        for name in ("delta_D", "relative_D", "relative_G", "relative_legacy_ratio"):
            interval(row["metrics"][name], name, adjusted=name == "delta_D")
    method = result["method"]
    if finite(method["confidence"], "confidence") != .95:
        raise ValueError("The lo95/hi95 figure labels require confidence=0.95")
    adjusted = finite(method["adjusted_confidence"], "adjusted_confidence")
    if not .95 < adjusted < 1:
        raise ValueError("adjusted_confidence must exceed 0.95 and be below 1")
    replicates = method["bootstrap_replicates"]
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates <= 0:
        raise ValueError("bootstrap_replicates must be a positive integer")
    return groups, contrasts


def configure_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 11,
        "axes.titlesize": 12, "axes.labelsize": 11,
        "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": "#6b7175", "axes.labelcolor": NEUTRAL,
        "text.color": NEUTRAL, "xtick.color": NEUTRAL, "ytick.color": NEUTRAL,
        "figure.facecolor": "white", "axes.facecolor": "white",
        "svg.fonttype": "none", "savefig.facecolor": "white",
    })


def guide_axes(ax: plt.Axes, *, near: bool, shade: bool = True) -> None:
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#e5e7e9", linewidth=.7)
    ax.axhline(0, color="#858b90", linewidth=.8, zorder=1)
    for x in (-3, 3):
        ax.axvline(x, color="#989da2", linewidth=.75, linestyle=":", zorder=1)
    if shade:
        ax.axvspan(-3, 3, facecolor="#e8ecef", alpha=.65, linewidth=0, zorder=0)
    if near:
        ax.set_xlim(-NEAR_LIMIT, NEAR_LIMIT)
        ax.set_xticks(NEAR_TICKS)
    else:
        ax.set_xlim(-42, 42)
        ax.set_xticks((-40, -20, 0, 20, 40))
    ax.set_xlabel("Incident-electron source x (mm)")


def draw_intervals(ax: plt.Axes, x: np.ndarray, estimates: np.ndarray,
                   lower: np.ndarray, upper: np.ndarray, *, color: str,
                   marker: str, linestyle: str = "-", cap: float = .10,
                   connect: bool = True, label: str | None = None) -> None:
    """Use endpoints directly: percentile bounds need not contain the estimate."""
    if connect:
        ax.plot(x, estimates, color=color, linestyle=linestyle, linewidth=1.05,
                alpha=.7, zorder=2)
    ax.vlines(x, lower, upper, colors=color, linewidth=1.1, zorder=3)
    ax.hlines(lower, x - cap, x + cap, colors=color, linewidth=1.05, zorder=3)
    ax.hlines(upper, x - cap, x + cap, colors=color, linewidth=1.05, zorder=3)
    ax.plot(x, estimates, color=color, marker=marker, linestyle="none", markersize=5.2,
            markeredgewidth=.9, markeredgecolor="white", label=label, zorder=4)


def arrays(rows: list[dict], metric: str, factor: float = 1.) -> tuple[np.ndarray, ...]:
    x = np.array([row["x_mm"] for row in rows], dtype=float)
    metrics = [row["metrics"][metric] for row in rows]
    return (x, *(factor * np.array([item[key] for item in metrics], dtype=float)
                 for key in ("estimate", "lo95", "hi95")))


def geometry_handles() -> list[Line2D | Patch]:
    handles: list[Line2D | Patch] = []
    for style in STYLES.values():
        handles.append(Line2D([], [], color=style["color"], marker=style["marker"],
                              linestyle=style["linestyle"], linewidth=1.1,
                              markersize=6, label=style["label"]))
    handles.append(Patch(facecolor="#e8ecef", edgecolor="none",
                         label="Dimple footprint along y = 0: −3 ≤ x ≤ 3 mm"))
    return handles


def bootstrap_note(result: dict) -> str:
    replicates = result["method"]["bootstrap_replicates"]
    return (f"200 independent events per geometry/point; independent event bootstrap "
            f"({replicates:,} resamples).")


def response_figure(result: dict, groups: dict[str, list[dict]]) -> plt.Figure:
    fig, axes = plt.subplots(3, 2, figsize=(13.5, 11), sharey="row")
    fig.subplots_adjust(left=.10, right=.98, top=.84, bottom=.12, hspace=.35, wspace=.14)
    fig.text(.10, .966, "Flat and dimple response along the sampled line",
             ha="left", va="baseline", fontsize=17, weight="bold")
    fig.text(.10, .934, bootstrap_note(result), fontsize=10.5)
    fig.legend(handles=geometry_handles(), loc="upper left", bbox_to_anchor=(.093, .912),
               ncol=3, frameon=False, columnspacing=1.4)
    specs = (("mean_D", 1., "Mean collected photons D\n(photons / incident electron)"),
             ("mean_G", 1., "Mean legacy generated photons G\n(photons / incident electron)"),
             ("legacy_D_over_G", 100., "Legacy ratio ΣD / ΣG (%)"))
    for row_index, (metric, factor, ylabel) in enumerate(specs):
        upper = max(item["metrics"][metric]["hi95"] * factor
                    for rows in groups.values() for item in rows)
        for column, near in enumerate((False, True)):
            ax = axes[row_index, column]
            guide_axes(ax, near=near, shade=near)
            for geometry in GEOMETRIES:
                style = STYLES[geometry]
                values = arrays(groups[geometry], metric, factor)
                draw_intervals(ax, *values, color=style["color"], marker=style["marker"],
                               linestyle=style["linestyle"], cap=.32 if not near else .09)
            ax.set_ylim(0, upper * 1.10 if upper > 0 else 1)
            ax.set_title(("Full sampled range" if not near else "Near the dimple opening")
                         if row_index == 0 else "", loc="left")
        axes[row_index, 0].set_ylabel(ylabel)
    fig.text(.10, .061, "Intervals: descriptive pointwise 95% CIs. Lines only guide the eye; x spacing is numeric.",
             fontsize=10)
    fig.text(.10, .038, "G retains the legacy birth definition; ΣD / ΣG is a ratio of totals, not a complete photon-birth efficiency.",
             fontsize=10)
    fig.text(.10, .015, "This nonuniform one-dimensional scan is not a tile-area average or a 2D uniformity measurement.", fontsize=10)
    return fig


def contrasts_figure(result: dict, contrasts: list[dict]) -> plt.Figure:
    fig, axes = plt.subplots(2, 1, figsize=(13.5, 9.2), gridspec_kw={"height_ratios": (1.15, 1)})
    fig.subplots_adjust(left=.11, right=.98, top=.80, bottom=.16, hspace=.49)
    fig.text(.11, .966, "Dimple–flat contrasts at the sampled positions",
             ha="left", va="baseline", fontsize=17, weight="bold")
    fig.text(.11, .934, bootstrap_note(result), fontsize=10.5)
    specs = (("relative_D", "#2166ac", "o", "-", "Collected D"),
             ("relative_G", "#d95f02", "s", "--", "Legacy generated G"),
             ("relative_legacy_ratio", "#59646b", "^", ":", "Legacy ΣD / ΣG"))
    handles = [Line2D([], [], color=color, marker=marker, linestyle=linestyle,
                      linewidth=1.1, label=label)
               for _, color, marker, linestyle, label in specs]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(.103, .912),
               ncol=3, frameon=False, columnspacing=1.8)
    upper = axes[0]
    guide_axes(upper, near=True)
    near_contrasts = [row for row in contrasts if abs(row["x_mm"]) <= NEAR_LIMIT]
    for metric, color, marker, linestyle, _ in specs:
        draw_intervals(upper, *arrays(near_contrasts, metric, 100.), color=color,
                       marker=marker, linestyle=linestyle, cap=.09)
    upper.set_ylabel("Relative change (%)\n100 × (dimple / flat − 1)")
    upper.set_title("Near field · descriptive pointwise 95% CIs", loc="left")

    lower = axes[1]
    lower.set_axisbelow(True)
    lower.grid(axis="y", color="#e5e7e9", linewidth=.7)
    lower.axhline(0, color="#858b90", linewidth=.85, zorder=1)
    category_x = np.arange(len(contrasts), dtype=float)
    values = [row["metrics"]["delta_D"] for row in contrasts]
    y = np.array([item["estimate"] for item in values])
    lo_adjusted = np.array([item["lo_adjusted"] for item in values])
    hi_adjusted = np.array([item["hi_adjusted"] for item in values])
    lower.vlines(category_x, lo_adjusted, hi_adjusted, color="#9ba3a9", linewidth=1.15, zorder=2)
    lower.hlines(lo_adjusted, category_x - .12, category_x + .12,
                 color="#9ba3a9", linewidth=1.15, zorder=2)
    lower.hlines(hi_adjusted, category_x - .12, category_x + .12,
                 color="#9ba3a9", linewidth=1.15, zorder=2)
    lower.vlines(category_x, [item["lo95"] for item in values], [item["hi95"] for item in values],
                 color=NEUTRAL, linewidth=3, zorder=3)
    lower.plot(category_x, y, color=NEUTRAL, marker="D", markersize=5,
               markeredgecolor="white", markeredgewidth=.65, linestyle="none", zorder=4)
    lower.set_xticks(category_x, [f"{row['x_mm']:g}" for row in contrasts])
    lower.set_xlim(-.65, len(contrasts) - .35)
    lower.set_xlabel("Source x (mm); positions shown at equal categorical spacing")
    lower.set_ylabel("Dimple − flat mean D\n(photons / incident electron)")
    level = 100 * result["method"]["adjusted_confidence"]
    lower.set_title(f"All 13 positions · dark 95% CI; light {level:.3f}% adjusted CI", loc="left")
    fig.text(.11, .083, f"Adjusted intervals use {level:.3f}% per comparison for the 13 primary mean-D contrasts.", fontsize=10)
    fig.text(.11, .057, "Near-field shading marks the dimple footprint, −3 to +3 mm; lines only guide the eye.", fontsize=10)
    fig.text(.11, .031, "Geometry samples are independent. Relative diagnostics and pointwise intervals are descriptive.", fontsize=10)
    return fig


def diagnostics_figure(result: dict, groups: dict[str, list[dict]]) -> plt.Figure:
    fig = plt.figure(figsize=(13.5, 9.5))
    grid = fig.add_gridspec(2, 2, left=.10, right=.98, top=.82, bottom=.17,
                           hspace=.47, wspace=.14, height_ratios=(1.05, 1))
    energy = fig.add_subplot(grid[0, :])
    blocks_flat = fig.add_subplot(grid[1, 0])
    blocks_dimple = fig.add_subplot(grid[1, 1], sharey=blocks_flat)
    fig.text(.10, .966, "Near-field source and seed-block diagnostics",
             ha="left", va="baseline", fontsize=17, weight="bold")
    fig.text(.10, .934, bootstrap_note(result), fontsize=10.5)
    fig.legend(handles=geometry_handles(), loc="upper left", bbox_to_anchor=(.093, .912),
               ncol=3, frameon=False, columnspacing=1.4)
    guide_axes(energy, near=True)
    for geometry in GEOMETRIES:
        style = STYLES[geometry]
        near_rows = [row for row in groups[geometry] if abs(row["x_mm"]) <= NEAR_LIMIT]
        draw_intervals(energy, *arrays(near_rows, "mean_electron_edep_mev"),
                       color=style["color"], marker=style["marker"], linestyle=style["linestyle"], cap=.07)
    energy.set_ylim(bottom=0)
    energy.set_ylabel("Mean e± energy deposited\nin tile (MeV / incident electron)")
    energy.set_title("Electron / positron energy deposition · descriptive pointwise 95% CIs", loc="left")

    block_max = 0.
    for ax, geometry in zip((blocks_flat, blocks_dimple), GEOMETRIES):
        style = STYLES[geometry]
        rows = [row for row in groups[geometry] if abs(row["x_mm"]) <= NEAR_LIMIT]
        x = [row["x_mm"] for row in rows]
        guide_axes(ax, near=True)
        for block_id in (0, 1):
            y = [next(block["mean_D"] for block in row["blocks"] if block["block"] == block_id)
                 for row in rows]
            block_max = max(block_max, max(y))
            ax.plot(x, y, color=style["color"], marker=style["marker"], markersize=5.4,
                    markerfacecolor=style["color"] if block_id == 0 else "white",
                    markeredgewidth=1.1, linewidth=1.05,
                    linestyle="-" if block_id == 0 else "--", label=f"Block {block_id} · 100 events")
        ax.set_title(style["label"], loc="left")
        ax.legend(loc="best", frameon=True, facecolor="white", edgecolor="#eceeef", fontsize=9)
        ax.set_ylim(0, block_max * 1.13 if block_max > 0 else 1)
    blocks_flat.set_ylabel("Block mean collected D\n(photons / incident electron)")
    blocks_dimple.tick_params(labelleft=False)
    fig.text(.10, .093, "Each block mean uses 100 individual events. These traces compare seed blocks; they have no interval bars.", fontsize=10)
    fig.text(.10, .067, "The inferential sample is 200 events per configuration, not two block means. Lines only guide the eye.", fontsize=10)
    fig.text(.10, .041, "Shading marks the dimple footprint along y = 0. Deposited energy and collected light are distinct measurements.", fontsize=10)
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result_path = args.result.resolve(strict=True)
    result = json.loads(result_path.read_text())
    groups, contrasts = validate(result)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    targets = [output / f"{name}.{extension}" for name in NAMES for extension in ("png", "svg")]
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise FileExistsError("Refusing to overwrite existing figure(s): " + ", ".join(existing))
    configure_style()
    figures = (("response", response_figure(result, groups)),
               ("contrasts", contrasts_figure(result, contrasts)),
               ("diagnostics", diagnostics_figure(result, groups)))
    for name, figure in figures:
        for extension in ("png", "svg"):
            target = output / f"{name}.{extension}"
            # Exclusive file creation retains the no-overwrite contract on races.
            with target.open("xb") as stream:
                figure.savefig(stream, format=extension, dpi=220, bbox_inches="tight", pad_inches=.12)
        plt.close(figure)
    print(json.dumps({"result": str(result_path), "figures": [str(path) for path in targets]}, indent=2))


if __name__ == "__main__":
    main()
