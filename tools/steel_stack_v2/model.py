#!/usr/bin/env python3
"""Explicit stack-v2 configuration matrices and deterministic independent seeds."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

SCHEMA_VERSION = "steel-stack-v2-configuration-v1"
STUDY_PRESET = "steel-module-stack-v2"
LAYOUTS = ("back-center", "edge-two", "back-two", "back-four")
THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
LAYERS = 10
SENSOR_STRIDE = 4
MAX_SEED = 2_147_483_646
LOCAL_CENTRES = {"back-center": ((0., 0.),), "edge-two": ((-25., 0.), (25., 0.)),
                 "back-two": ((-25., -25.), (25., 25.)),
                 "back-four": ((-25., -25.), (-25., 25.), (25., -25.), (25., 25.))}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def configuration_hash(configuration):
    return hashlib.sha256(canonical_json(configuration)).hexdigest()


def make_configuration(layout, thickness_mm, gap_mm, optical_numerics="legacy", corner_scale=0):
    """Construct a v2 case. No default gap or default layout is permitted."""
    require(layout in LAYOUTS, f"Unsupported layout: {layout}")
    require(not isinstance(thickness_mm,bool) and not isinstance(gap_mm,bool), "Dimensions must be numeric values, not booleans")
    thickness, gap = float(thickness_mm), float(gap_mm)
    require(math.isfinite(thickness) and thickness in THICKNESSES_MM, "Unsupported tile thickness")
    require(math.isfinite(gap) and gap >= .5, "readout gap must be explicitly finite and >= 0.5 mm")
    core_length = LAYERS * (40. + thickness) + (LAYERS - 1) * gap
    source_z = core_length / 2 + 1.5
    require(source_z < 500., "Source or stack would exceed the fixed world extent")
    require((optical_numerics == "legacy" and corner_scale == 0) or
            (optical_numerics in ("painted-corner-v1","painted-corner-v2") and corner_scale in (16,32,64)), "Invalid numerical profile/scale")
    count = len(LOCAL_CENTRES[layout])
    return {"schema_version": SCHEMA_VERSION, "study_preset": STUDY_PRESET,
            "optical_numerics": {"profile":optical_numerics,"scale":corner_scale},
            "layout": layout, "tile_thickness_mm": thickness, "gap_mm": gap,
            "layers": LAYERS, "internal_gap_count": LAYERS-1,
            "module_pitch_mm": 40. + thickness + gap, "core_length_mm": core_length,
            "steel_full_size_mm": [500., 500., 40.], "tile_full_size_mm": [100., 100., thickness],
            "world_full_size_mm": [1000., 1000., 1000.], "sipm_full_size_mm": [2.4, 2.4, .5],
            "sipm_face": "+X" if layout == "edge-two" else "-Z",
            "sensor_local_uv_mm": [list(pair) for pair in LOCAL_CENTRES[layout]],
            "sensors_per_layer": count, "sensor_count": count*LAYERS, "sensor_copy_stride": SENSOR_STRIDE,
            "active_sensor_copies": [SENSOR_STRIDE*layer+sensor for layer in range(LAYERS) for sensor in range(count)],
            "source": {"particle": "neutron", "kinetic_energy_mev": 1000., "profile": "point",
                       "direction": [0., 0., -1.], "position_mm": [0., 0., source_z]},
            "optical_policy": "retain-geant4-11.4.2-default-registration-and-existing-macro-switches",
            "surface": "polishedfrontpainted-ej510", "coupling": "undimpled-zero-gap-ej550-proxy"}


def validate_configuration(configuration):
    numerics = configuration.get("optical_numerics", {"profile":"legacy","scale":0})
    expected = make_configuration(configuration["layout"], configuration["tile_thickness_mm"], configuration["gap_mm"],
                                  numerics["profile"], numerics["scale"])
    if "optical_numerics" not in configuration: expected.pop("optical_numerics") # archived v1 schema, always legacy
    require(configuration == expected, "Configuration fields differ from the explicit v2 contract")
    return configuration


def configuration_id(configuration):
    validate_configuration(configuration)
    gap = format(configuration["gap_mm"], ".12g").replace(".", "p")
    return f"t{int(configuration['tile_thickness_mm']):02d}-g{gap}-{configuration['layout']}-{configuration_hash(configuration)[:10]}"


def make_matrix(matrix, gap_values_mm):
    gaps = [float(value) for value in gap_values_mm]
    require(len(gaps) == 2 and len(set(gaps)) == 2, "The matrix requires two explicit distinct gap values")
    require(matrix in ("sensitivity", "full"), "matrix must be sensitivity or full")
    thicknesses = (4, 24) if matrix == "sensitivity" else THICKNESSES_MM
    layouts = ("back-center", "edge-two") if matrix == "sensitivity" else LAYOUTS
    return [make_configuration(layout, thickness, gap) for gap in sorted(gaps)
            for thickness in thicknesses for layout in layouts]


def prepare_tasks(configs, *, events_per_task, blocks, campaign_seed, total_event_budget,
                  stage="science", excluded_seeds=()):
    for key, value in (("events_per_task", events_per_task), ("blocks", blocks), ("total_event_budget", total_event_budget)):
        require(isinstance(value, int) and not isinstance(value, bool) and value > 0, f"{key} must be an explicit positive integer")
    require(isinstance(campaign_seed, int) and not isinstance(campaign_seed, bool), "campaign_seed must be explicit")
    require(stage and all(char.isalnum() or char in "-_" for char in stage), "Unsafe stage")
    configs = list(configs)
    require(configs, "No configurations")
    hashes = [configuration_hash(validate_configuration(config)) for config in configs]
    require(len(hashes) == len(set(hashes)), "Duplicate configurations")
    events = len(configs) * blocks * events_per_task
    require(events <= total_event_budget, f"Planned {events} events exceed explicit budget {total_event_budget}")
    used = set(int(value) for value in excluded_seeds)
    require(all(0 < value <= MAX_SEED for value in used), "Excluded seeds outside Geant4 range")
    result = []
    for configuration in configs:
        identity = configuration_id(configuration)
        for block in range(blocks):
            seeds = []
            for slot in (0, 1):
                nonce = 0
                while True:
                    material = canonical_json([STUDY_PRESET, campaign_seed, configuration_hash(configuration), stage, block, slot, nonce])
                    candidate = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % MAX_SEED + 1
                    if candidate not in used:
                        used.add(candidate); seeds.append(candidate); break
                    nonce += 1
            result.append({"task_id": f"{stage}-{identity}-b{block:03d}", "stage": stage,
                           "configuration_id": identity, "configuration_hash": configuration_hash(configuration),
                           "config": copy.deepcopy(configuration), "events": events_per_task, "block": block,
                           "seed1": seeds[0], "seed2": seeds[1], "campaign_seed": campaign_seed,
                           "independent_sample": True, "reproducibility_check": False})
    return result


def repeat_task(task, new_task_id):
    require(new_task_id != task["task_id"] and all(char.isalnum() or char in "-_." for char in new_task_id), "Invalid repeat task ID")
    result = copy.deepcopy(task)
    for key in ("run_dir", "completed", "accepted", "root_sha256"):
        result.pop(key, None)
    result.update(task_id=new_task_id, repeat_of=task["task_id"], independent_sample=False, reproducibility_check=True)
    return result


def validate_tasks(tasks, *, total_event_budget=None):
    require(tasks and len({task["task_id"] for task in tasks}) == len(tasks), "Empty or duplicate task IDs")
    by_id = {task["task_id"]: task for task in tasks}
    independent_seeds = []
    for task in tasks:
        config = validate_configuration(task["config"])
        require(task["configuration_hash"] == configuration_hash(config) and task["configuration_id"] == configuration_id(config), "Task configuration identity mismatch")
        require(isinstance(task["events"], int) and task["events"] > 0, "Invalid task event count")
        require(all(isinstance(task[key], int) and 0 < task[key] <= MAX_SEED for key in ("seed1", "seed2")), "Invalid task seeds")
        if task.get("repeat_of"):
            original = by_id.get(task["repeat_of"])
            require(original is not None and not original.get("repeat_of"), "Invalid repeat reference")
            require(task.get("independent_sample") is False and task.get("reproducibility_check") is True, "Unlabelled repeat")
            for key in ("configuration_hash", "events", "seed1", "seed2"):
                require(task[key] == original[key], f"Repeat changes {key}")
        else:
            require(task.get("independent_sample") is True and task.get("reproducibility_check") is False, "Independent task flags missing")
            independent_seeds.extend((task["seed1"], task["seed2"]))
    require(len(independent_seeds) == len(set(independent_seeds)), "Independent task seed collision")
    total = sum(task["events"] for task in tasks)
    if total_event_budget is not None:
        require(total <= total_event_budget, "Task events exceed budget")
    return {"tasks": len(tasks), "events": total, "independent_tasks": sum(not task.get("repeat_of") for task in tasks),
            "unique_independent_seeds": len(set(independent_seeds))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", required=True, choices=("sensitivity", "full"))
    parser.add_argument("--gap-mm", required=True, type=float, action="append")
    parser.add_argument("--configs-only", action="store_true", help="Write only the explicit configuration matrix, with no scientific task count")
    parser.add_argument("--events-per-task", type=int)
    parser.add_argument("--blocks", type=int)
    parser.add_argument("--campaign-seed", type=int)
    parser.add_argument("--event-budget", type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    configs = make_matrix(args.matrix, args.gap_mm)
    if args.configs_only:
        require(all(value is None for value in (args.events_per_task,args.blocks,args.campaign_seed,args.event_budget)),
                "configs-only does not accept event/seed/budget parameters")
        tasks = None
    else:
        tasks = prepare_tasks(configs, events_per_task=args.events_per_task, blocks=args.blocks,
                              campaign_seed=args.campaign_seed, total_event_budget=args.event_budget)
    require(not args.output_dir.exists(), "Output directory already exists")
    args.output_dir.mkdir(parents=True)
    (args.output_dir/"configurations.json").write_text(json.dumps(configs, indent=2)+"\n")
    if tasks is not None:
        (args.output_dir/"tasks.json").write_text(json.dumps(tasks, indent=2)+"\n")
        print(json.dumps(validate_tasks(tasks, total_event_budget=args.event_budget), indent=2))
    else:
        print(json.dumps({"configurations":len(configs),"scientific_tasks_generated":False}))


if __name__ == "__main__":
    main()
