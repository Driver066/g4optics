#!/usr/bin/env python3
"""Plot only the registered source-position design; no simulated results."""
from __future__ import annotations
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

import model
import pilot_model

COLORS = {"center":"#111827", "axis":"#2563eb", "diagonal":"#d97706", "symmetry":"#7c3aed"}
MARKERS = {"center":"*", "axis":"o", "diagonal":"s", "symmetry":"x"}


def plot(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    paths = [output/"pilot-positions.png", output/"pilot-positions.svg"]
    if any(path.exists() for path in paths):
        raise ValueError("Choose a new directory; existing figures are preserved")
    points = pilot_model.positions()
    plt.rcParams.update({"font.size":10, "axes.spines.top":False, "axes.spines.right":False,
                         "svg.hashsalt":"issue9-pilot-v1"})
    fig, axes = plt.subplots(1,2,figsize=(12,5.9),layout="constrained",
                            gridspec_kw={"width_ratios":[1,1.15]})
    for ax in axes:
        ax.set_aspect("equal")
        ax.set_xlabel("Source x (mm)"); ax.set_ylabel("Source y (mm)")
        ax.grid(color="#e5e7eb",linewidth=.7,zorder=0)
        for kind in COLORS:
            selected=[p for p in points if p["kind"]==kind]
            ax.scatter([p["x_mm"] for p in selected],[p["y_mm"] for p in selected],
                       c=COLORS[kind],marker=MARKERS[kind],s=70 if kind=="center" else 28,
                       linewidths=1.4,zorder=4,label=kind.capitalize())
    axes[0].add_patch(Rectangle((-50,-50),100,100,fill=False,ec="#475569",lw=1.4))
    axes[0].set(xlim=(-53,53),ylim=(-53,53),title="25 shared positions on the 100 × 100 mm tile")
    axes[0].add_patch(Rectangle((-4,-4),8,8,fill=False,ec="#6b7280",ls="--",lw=1))
    for p in points:
        if p["kind"] != "symmetry" and max(abs(p["x_mm"]),abs(p["y_mm"]))>=10:
            axes[0].annotate(f"({p['x_mm']:g}, {p['y_mm']:g})",(p["x_mm"],p["y_mm"]),
                             xytext=(4,-13 if p["kind"]=="axis" and p["x_mm"]<48 else 5),
                             textcoords="offset points",fontsize=7.5,
                             ha="right" if p["x_mm"]>35 else "left")
    axes[0].legend(loc="lower left",framealpha=.95,fontsize=8)
    ax=axes[1]
    ax.set(xlim=(-4,4),ylim=(-4,4),title="Central detail: aperture and sensor footprints")
    ax.add_patch(Circle((0,0),3,fill=False,color="#b45309",lw=1.5,label="Hemisphere opening, r = 3 mm"))
    a=model.CUBE_SIDE_MM
    ax.add_patch(Rectangle((-a/2,-a/2),a,a,fill=False,ec="#15803d",lw=1.5,label=f"Cubic well, a = {a:.4f} mm"))
    ax.add_patch(Rectangle((-1,-1),2,2,fill=False,ec="#2563eb",ls=":",lw=1.4,label="Small SiPM, 2 × 2 mm"))
    ax.add_patch(Rectangle((-3,-3),6,6,fill=False,ec="#7c3aed",ls="--",lw=1.4,label="Large SiPM, 6 × 6 mm"))
    for p in points:
        if p["kind"] in ("axis","diagonal") and p["x_mm"]<4:
            ax.annotate(f"{p['x_mm']:g}",(p["x_mm"],p["y_mm"]),
                        xytext=(0,-13) if p["kind"]=="axis" else (-5,6),
                        textcoords="offset points",fontsize=8,ha="center" if p["kind"]=="axis" else "right")
    handles,labels=ax.get_legend_handles_labels()
    ax.legend(handles[-4:],labels[-4:],loc="lower left",fontsize=8,framealpha=.96)
    fig.suptitle("Independent mechanism pilot: 7 configurations × 25 positions × 200 electrons",fontsize=14)
    fig.supxlabel("Prepared source positions at z = 18 mm. Design only; no simulation results or area-average weights.",fontsize=10)
    for path in paths:
        fig.savefig(path,dpi=180,metadata={"Creator":"g4optics issue9 pilot design"})
    plt.close(fig)
    print("\n".join(str(p) for p in paths))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    plot(parser.parse_args().output.resolve())
