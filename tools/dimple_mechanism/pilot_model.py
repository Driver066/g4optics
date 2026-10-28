"""Frozen-design issue #9 pilot inputs; importing/generating never runs Geant4."""
from __future__ import annotations

import hashlib
import json
import math
import re

import model as geometry_model

MODEL_ID = "issue9-dimple-mechanism-pilot-v1"
TEMPLATE = geometry_model.TEMPLATE
TEMPLATE_SHA256 = "8d308bcc8d6cf07262b3f304cca55b3ddb152350b19fe68bb4f27822e1140da3"
EVENTS_PER_TASK = 100
BLOCKS = 2
TOTAL_TASKS = 350
TOTAL_EVENTS = 35000
SEED_BASE = 2026102801
SOURCE_Z_MM = 18.
CONFIGURATIONS = geometry_model.CONFIGURATIONS


def positions() -> list[dict]:
    """Return the agreed 25 diagnostic positions in stable point-ID order."""
    coordinates = [("center", 0., 0.)]
    coordinates += [("axis", x, 0.) for x in (.8, 1.2, 1.7, 2.1, 2.8, 3.2, 10., 40., 48.)]
    coordinates += [("diagonal", x, x) for x in (.8, 1.2, 1.7, 2.1, 2.3, 3.2, 10., 40., 48.)]
    coordinates += [("symmetry", x, y) for x, y in
                    ((-10., 0.), (0., 10.), (0., -10.), (-10., -10.), (-10., 10.), (10., -10.))]
    return [dict(point_id=f"p{i:02d}", kind=kind, label=f"{kind} ({x:g}, {y:g}) mm", x_mm=x, y_mm=y)
            for i, (kind, x, y) in enumerate(coordinates)]


POSITIONS = tuple(positions())


def tasks() -> list[dict]:
    """Two independent blocks; each block contains A--G at all 25 positions."""
    result = []
    for block in range(BLOCKS):
        for config in CONFIGURATIONS:
            for point in positions():
                index = len(result)
                name = f"pilot-b{block}-{config['key']}-{point['point_id']}"
                result.append(dict(name=name, task_id=name, stage="pilot", configuration=config["key"],
                                   key=config["key"], config=dict(config), point_id=point["point_id"],
                                   x_mm=point["x_mm"], y_mm=point["y_mm"], block=block,
                                   events=EVENTS_PER_TASK, seeds=[SEED_BASE+2*index, SEED_BASE+2*index+1]))
    return result


def validate_task(task: dict) -> None:
    """Accept only registered scientific inputs and optional bound SHA metadata."""
    if not isinstance(task, dict):
        raise ValueError("Pilot task must be a mapping")
    expected = next((item for item in tasks() if item["task_id"] == task.get("task_id")), None)
    if expected is None:
        raise ValueError("Unregistered pilot task")
    extra = set(task)-set(expected)
    if extra-{"macro_sha256", "input_manifest_sha256"}:
        raise ValueError("Unexpected pilot task fields")
    for key in extra:
        if not isinstance(task[key], str) or re.fullmatch(r"[0-9a-f]{64}", task[key]) is None:
            raise ValueError(f"Invalid pilot identity digest: {key}")
    for key in ("events", "block"):
        if type(task.get(key)) is not int:
            raise ValueError(f"Pilot {key} must be an integer")
    for key in ("x_mm", "y_mm"):
        if type(task.get(key)) not in (int, float) or not math.isfinite(task[key]):
            raise ValueError(f"Pilot {key} must be finite")
    seeds = task.get("seeds")
    if not isinstance(seeds, list) or len(seeds) != 2 or any(type(seed) is not int or not 0 < seed < 2**31 for seed in seeds):
        raise ValueError("Pilot seeds must be a valid integer pair")
    scientific = {key: task.get(key) for key in expected}
    # JSON comparison also prevents bool/int coercion in the geometry flags.
    if json.dumps(scientific, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True):
        raise ValueError("Pilot task differs from the registered design")


def macro(template: str, task: dict) -> str:
    """Generate a fixed 100-event pilot macro without changing the smoke gate."""
    validate_task(task)
    if hashlib.sha256(template.encode()).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Pilot optical template differs from the accepted historical source")
    config = task["config"]
    geometry_model.validate_configuration(config)
    seed1, seed2 = task["seeds"]
    return f"""# {MODEL_ID}; registered input {task['task_id']}.
# This file is a prepared input. Generation is not execution authorization.
/control/verbose 0
/run/verbose 0
/tracking/verbose 0
/tracking/storeTrajectory 0
/random/setSeeds {seed1} {seed2}
{geometry_model.material_commands(template)}
/opnovice2/tank/size 100 100 24 mm
/opnovice2/tank/bottomCavity false
/opnovice2/absorber/enabled false
/opnovice2/stack/enabled false
/opnovice2/stack/layoutStudy false
/opnovice2/grease/enabled false
/opnovice2/sipm/layout single
/opnovice2/sipm/face -Z
/opnovice2/sipm/localPosition 0 0 0 mm
/opnovice2/sipm/size {config['sensor_mm']:g} {config['sensor_mm']:g} 0.5 mm
/opnovice2/dimple/enabled {str(config['enabled']).lower()}
/opnovice2/dimple/radius 3 mm
/opnovice2/dimple/mode {config['shape']}
/opnovice2/dimple/sipmMode external
/opnovice2/dimple/frontCompensation {str(config['front']).lower()}
/opnovice2/diagnostics/issue9Geometry true
/opnovice2/diagnostics/dimpleOptical true
/opnovice2/diagnostics/saveRandomState true
/run/initialize
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
/gps/pos/centre {task['x_mm']:g} {task['y_mm']:g} 18 mm
/gps/direction 0 0 -1
/gps/energy 1.0 MeV
# Reset after geometry sampling. Geometries/blocks use independent seed pairs.
/random/setSeeds {seed1} {seed2}
/run/printProgress 100
/run/beamOn 100
/control/echo ISSUE9_PILOT_DONE
"""
