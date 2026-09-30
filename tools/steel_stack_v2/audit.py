#!/usr/bin/env python3
"""Read-only event-ledger and runtime auditing for steel-stack v2 outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex

import numpy as np
import uproot

from model import LAYERS, SENSOR_STRIDE, configuration_hash, require, validate_configuration

TREES = ("stack_event_v2", "stack_layer_v2", "stack_sensor_v2", "stack_photon_flow_v2")
FATES = ("sipm", "bulk_tile", "bulk_steel", "bulk_world", "bulk_other", "boundary_absorption",
         "boundary_detection", "world_escape", "wls", "wls2", "no_rindex", "other")
BIRTH_FIELDS = ("births_total", "births_primary", "births_nonoptical_parent", "births_optical_parent",
                "births_scintillation", "births_cerenkov", "births_wls", "births_wls2", "births_other")
HARD_ZERO = ("births_unknown", "pending_births", "unmatched_starts", "duplicate_births", "duplicate_starts",
             "duplicate_finalizations", "unresolved_tracks", "parent_mismatches", "unknown_birth_origins",
             "unknown_root_origins", "inactive_sensor_hits", "duplicate_steps", "fate_no_rindex", "fate_other",
             "boundary_no_rindex", "collected_step_no_rindex")
ENERGY_FIELDS = ("tile_nonoptical_edep_mev", "tile_optical_edep_mev", "legacy_tile_edep_mev",
                 "steel_nonoptical_edep_mev", "steel_optical_edep_mev", "legacy_steel_edep_mev")
PHYSICS_STATE = {"electron_scintillation_active": True, "optical_scintillation_active": True,
                 "optical_wls2_active": True, "geant4_version": "11.4.2", "electron_cerenkov_active": False,
                 "optical_absorption_active": True, "optical_boundary_active": True,
                 "optical_rayleigh_active": False, "optical_miehg_active": False, "optical_wls_active": False,
                 "scintillation_by_particle_type": False, "scintillation_track_info": False,
                 "scintillation_finite_rise_time": True, "scintillation_stack_photons": True,
                 "scintillation_track_secondaries_first": True, "kill_on_second_surface": False}


def sha256(path):
    path = Path(path)
    require(path.is_file(), f"Missing file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file_hash(path, expected):
    require(sha256(path) == expected, f"File hash mismatch: {path}")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def one(paths, description):
    values = list(paths)
    require(len(values) == 1, f"Expected one {description}, found {len(values)}")
    return values[0]


def tree_arrays(tree):
    return {name: tree[name].array(library="np") for name in tree.keys()}


def require_fields(values, fields, context):
    require(set(fields) <= set(values), f"Missing {context} fields: {sorted(set(fields)-set(values))}")


def integers(value, name, minimum=0):
    value = np.asarray(value)
    require(np.isfinite(value).all() and np.equal(value, np.floor(value)).all(), f"Invalid integer {name}")
    if minimum is not None:
        require((value >= minimum).all(), f"Negative {name}")
    return value.astype(np.int64)


def equal(a, b, message):
    require(np.array_equal(a, b), message)


def close(a, b, message):
    require(np.allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=False), message)


def check_legacy_collection(generated, detected, collection, validity=None, context="legacy"):
    generated,detected,collection=np.broadcast_arrays(np.asarray(generated,dtype=float),
        np.asarray(detected,dtype=float),np.asarray(collection,dtype=float))
    valid=generated>0
    require(np.allclose(collection[valid],detected[valid]/generated[valid],rtol=1e-12,atol=1e-15),
            f"{context}: collection ratio differs from D/legacy G")
    require(np.isnan(collection[~valid]).all(),f"{context}: zero legacy G requires NaN collection")
    if validity is not None:
        equal(np.broadcast_to(np.asarray(validity),valid.shape),valid.astype(int),f"{context}: collection validity flag differs")


def check_empty_legacy_stack_trees(root):
    for name in ("stack_layers","stack_transfers"):
        require(name not in root or int(root[name].num_entries)==0,f"v2 populated legacy tree {name}")


def event_order(values, events, context):
    require_fields(values, ("event_id",), context)
    ids = integers(values["event_id"], f"{context}.event_id")
    equal(np.sort(ids), np.arange(events), f"{context}: duplicate/missing/out-of-range event IDs")
    order = np.argsort(ids)
    return {key: value[order] for key, value in values.items()}


def check_log(path, events):
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    require(re.search(r"Geant4 version Name:.*geant4-11-04-patch-02", content), "Geant4 11.4.2 banner missing")
    block_pattern = re.compile(r"^[^\n]*G4Exception-START[^\n]*\n(.*?)^[^\n]*G4Exception-END[^\n]*(?:\n|$)", re.M | re.S)
    expected = ("*** G4Exception : Analysis_W001 issued by : G4RootNtupleFileManager::SetNtupleMergingMode "
                "Merging ntuples is not applicable in sequential application. Setting was ignored. "
                "*** This is just a warning message. ***")
    allowed = 0
    for block in block_pattern.finditer(content):
        require(" ".join(block.group(1).split()) == expected, f"Unexpected Geant4 exception: {block.group(1).strip()[:500]}")
        allowed += 1
    remainder = block_pattern.sub("", content)
    require("G4Exception" not in remainder, "Malformed or unclassified Geant4 exception")
    bad = [line for line in remainder.splitlines() if re.search(
        r"\bERROR\b|\bFatal\w*\b|COMMAND\s+NOT\s+FOUND|command.*not found|Aborting execution|"
        r"Segmentation (?:fault|violation)|Overlap (?:is )?detected|Overlap with volume|GeomVol1002", line, re.I)]
    require(not bad, f"Simulation log errors: {bad[:4]}")
    reported = re.findall(r"Number of events:\s*(\d+)\b", content)
    require(reported and int(reported[-1]) == events, "Completed event count missing or wrong")
    require(re.search(r"close file\s*:.*- done", content), "ROOT completion marker missing")
    return {"geant4_version": "11.4.2", "serial_ntuple_warning_count": allowed}


def macro_commands(path):
    commands = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        tokens = shlex.split(line, comments=True)
        if tokens:
            commands.append(tokens)
    return commands


def check_macro(path, config, accounting_enabled, expected_seeds):
    commands = macro_commands(path)
    mapping = {tokens[0]: tokens[1:] for tokens in commands}
    fixed = {"/opnovice2/stack/model": ["v2"], "/opnovice2/stack/enabled": ["true"],
             "/opnovice2/stack/layers": ["10"],
             "/opnovice2/sipm/layout": ["single" if config["layout"] == "back-center" else config["layout"]],
             "/opnovice2/diagnostics/stackPhotonAccounting": [str(accounting_enabled).lower()],
             "/gps/particle": ["neutron"], "/gps/pos/type": ["Point"],
             "/process/optical/processActivation": None}
    for key, expected in fixed.items():
        if expected is not None:
            require(mapping.get(key) == expected, f"Macro identity differs: {key}")
    numerics=config.get("optical_numerics")
    if numerics is not None:
        require(mapping.get("/opnovice2/numerics/mode")==[numerics["profile"]], "Macro numerical profile differs")
        require(mapping.get("/opnovice2/numerics/cornerScale")==[str(numerics["scale"])], "Macro numerical scale differs")
        require("/opnovice2/numerics/probeFile" not in mapping, "Diagnostic rays cannot pass neutron production audit")
    gap = mapping.get("/opnovice2/stack/readoutGap", [])
    require(len(gap) == 2 and gap[1] == "mm" and float(gap[0]) == config["gap_mm"], "Macro readout gap differs")
    for tokens in commands:
        require(not (tokens[0] == "/process/inactivate" and "Scintillation" in tokens), "Scintillation was disabled")
    if expected_seeds is not None:
        require(mapping.get("/random/setSeeds") == [str(value) for value in expected_seeds], "Macro seed identity differs")
    switches = {tokens[1]: tokens[2] for tokens in commands if tokens[0] == "/process/optical/processActivation" and len(tokens) == 3}
    expected_switches = {"Cerenkov": "false", "Scintillation": "true", "OpAbsorption": "true", "OpBoundary": "true",
                         "OpRayleigh": "false", "OpMieHG": "false", "OpWLS": "false"}
    require(all(switches.get(key) == value for key, value in expected_switches.items()), "Existing optical macro switches changed")
    require("OpWLS2" not in switches or switches["OpWLS2"] == "true", "Default OpWLS2 disabled")
    return {"macro_sha256": sha256(path), "optical_switches": switches}


def check_physics_state(path):
    state = read_json(path)
    for key, expected in PHYSICS_STATE.items():
        if isinstance(expected, bool):
            require(state.get(key) is expected, f"Actual physics state differs: {key}")
        else:
            require(state.get(key) == expected, f"Actual physics state differs: {key}")
    return state


def check_runtime(path, config, accounting_enabled):
    runtime = read_json(path)
    expected = {"schema_version": "steel-stack-v2-runtime-v1", "model": "v2", "run_manager": "Serial", "layout": config["layout"],
                "layers": 10, "readout_gap_mm": config["gap_mm"], "core_length_mm": config["core_length_mm"],
                "copy_stride": 4, "active_sensors_per_layer": config["sensors_per_layer"],
                "accounting_enabled": accounting_enabled}
    for key, value in expected.items():
        require(runtime.get(key) == value, f"Actual runtime identity differs: {key}")
    for key in ("tile_full_size_mm", "steel_full_size_mm", "world_full_size_mm"):
        close(runtime[key], config[key], f"Actual runtime dimensions differ: {key}")
    pitch, length, thickness = config["module_pitch_mm"], config["core_length_mm"], config["tile_thickness_mm"]
    tiles = np.array([[0.,0.,length/2-layer*pitch-40-thickness/2] for layer in range(10)])
    steels = np.array([[0.,0.,length/2-layer*pitch-20] for layer in range(10)])
    close(runtime["tile_centers_mm"], tiles, "Actual tile coordinates differ")
    close(runtime["steel_centers_mm"], steels, "Actual steel coordinates differ")
    sensors = runtime["sensor_map"]
    require(len(sensors) == config["sensor_count"], "Actual sensor cardinality differs")
    require(sorted(row["global_copy"] for row in sensors) == config["active_sensor_copies"], "Actual sensor IDs differ")
    for row in sensors:
        layer, local = row["layer"], row["local_sensor"]
        require(0 <= layer < 10 and 0 <= local < config["sensors_per_layer"] and row["global_copy"] == 4*layer+local,
                "Actual sensor copy mapping differs")
        u,v = config["sensor_local_uv_mm"][local]
        center = [50.25,u,tiles[layer,2]+v] if config["layout"] == "edge-two" else [u,v,tiles[layer,2]-thickness/2-.25]
        close(row["center_mm"], center, "Actual sensor coordinates differ")
        half_size = [.25,1.2,1.2] if config["layout"] == "edge-two" else [1.2,1.2,.25]
        close(row["half_size_mm"], half_size, "Actual sensor world-axis dimensions differ")
        require(row["face"] == config["sipm_face"], "Actual sensor face differs")
    numerics=config.get("optical_numerics")
    if numerics is not None:
        observed=runtime.get("optical_numerics", {})
        require(all(observed.get(k)==v for k,v in numerics.items()), "Runtime numerical identity differs")
        require(observed.get("diagnostic_primary") is False, "Diagnostic ray source cannot pass neutron audit")
        require(0 < observed.get("surface_tolerance_mm",0) < 1.e-8, "Unexpected surface tolerance")
    source = runtime["source"]
    for key, value in {"particle":"neutron", "position_distribution":"Point", "energy_distribution":"Mono",
                       "angular_distribution":"planar", "number_of_sources":1, "primaries_per_event":1}.items():
        require(source.get(key) == value, f"Actual source identity differs: {key}")
    close(source["energy_mev"], 1000., "Actual source energy differs")
    close(source["position_mm"], config["source"]["position_mm"], "Actual source coordinates differ")
    close(source["direction"], [0.,0.,-1.], "Actual source direction differs")
    physics = runtime["physics"]
    require(physics.get("requested_policy") == "legacy-steel-optical-macro", "Actual optical policy differs")
    switches = {"Cerenkov":False,"Scintillation":True,"OpAbsorption":True,"OpBoundary":True,
                "OpRayleigh":False,"OpMieHG":False,"OpWLS":False,"OpWLS2":True}
    for key, value in switches.items():
        require(physics["actual_process_activation"].get(key) is value, f"Actual process activation differs: {key}")
    require(physics.get("opticalphoton_scintillation_active") is True, "Default optical Scintillation is not active")
    for key in ("scintillation_by_particle_type", "scintillation_track_info", "scintillation_finite_rise_time",
                "scintillation_stack_photons", "scintillation_track_secondaries_first", "kill_on_second_surface"):
        require(physics.get(key) is PHYSICS_STATE[key], f"Actual optical parameter differs: {key}")
    return runtime


def check_run_config(saved, config, accounting_enabled, expected_seeds):
    require(saved.get("study_preset") == "steel-module-stack-v2", "Wrong run preset")
    stack = saved["stack"]
    if "optical_numerics" in config:
        require(stack.get("optical_numerics")==config["optical_numerics"], "Run configuration numerical identity differs")
    required = {"schema_version": "steel-module-stack-v2", "layers": 10, "readout_gap_mm": config["gap_mm"],
                "internal_gap_count": 9, "stack_length_mm": config["core_length_mm"],
                "source_z_mm": config["source"]["position_mm"][2], "sensor_copy_stride": 4,
                "sensors_per_layer": config["sensors_per_layer"], "active_global_copies": config["active_sensor_copies"],
                "accounting_enabled": accounting_enabled}
    for key, value in required.items():
        require(stack.get(key) == value, f"Run configuration geometry/accounting mismatch: {key}")
    require(saved["sipm"]["study_layout"] == config["layout"], "Run layout mismatch")
    if expected_seeds is not None:
        require([saved["random"][key] for key in ("seed1", "seed2")] == list(expected_seeds), "Run seed identity mismatch")


def sparse_sum(flow, mask, events):
    return np.bincount(flow["event_id"], weights=flow["photon_count"]*mask, minlength=events).astype(np.int64)


def check_origin(flow, prefix, config):
    cls, layer, sensor = (flow[f"{prefix}_{field}"] for field in ("class", "layer", "sensor"))
    require(np.isin(cls, [1, 2, 3, 4]).all(), f"Unknown {prefix} origin class")
    material = np.isin(cls, [1, 2])
    require(((layer[material] >= 0) & (layer[material] < 10)).all() and (sensor[material] == -1).all(), f"Invalid {prefix} material identity")
    world = cls == 3
    require((layer[world] == -1).all() and (sensor[world] == -1).all(), f"Invalid {prefix} world identity")
    sipm = cls == 4
    require(np.isin(sensor[sipm], config["active_sensor_copies"]).all(), f"Inactive {prefix} sensor origin")
    equal(layer[sipm], sensor[sipm]//4, f"{prefix} sensor/layer identity mismatch")


def check_ledgers(scan, event, layer, sensor, flow, config, events):
    event = event_order(event, events, "stack_event_v2")
    require_fields(event, ("run_id", "layers", "sensors_per_layer", "sensor_copy_stride", "generated_legacy", "detected_legacy",
                          *BIRTH_FIELDS, *HARD_ZERO, *ENERGY_FIELDS, "tracks_started", "tracks_finalized", "primary_optical_started",
                          "births_tile", "births_steel", "births_world", "births_sipm", *["fate_"+fate for fate in FATES]), "event")
    for key, value in event.items():
        if key.endswith("_mev"):
            require(np.isfinite(value).all() and (value >= -1e-12).all(), f"Invalid event energy {key}")
        else:
            event[key] = integers(value, f"event.{key}")
    require(len(set(event["run_id"])) == 1, "Task contains multiple run IDs")
    for key, value in (("layers", 10), ("sensors_per_layer", config["sensors_per_layer"]), ("sensor_copy_stride", 4)):
        require((event[key] == value).all(), f"Event metadata differs: {key}")
    for key in HARD_ZERO:
        require((event[key] == 0).all(), f"Hard ledger failure: {key}")
    require((event["primary_optical_started"] == 0).all() and (event["births_primary"] == 0).all(), "Unexpected optical primary for neutron source")
    b = event["births_total"]
    equal(b, event["births_primary"]+event["births_nonoptical_parent"]+event["births_optical_parent"], "Birth parent partition fails")
    equal(b, sum(event[key] for key in ("births_primary", "births_scintillation", "births_cerenkov", "births_wls", "births_wls2", "births_other")), "Birth creator partition fails")
    equal(b, sum(event["births_"+key] for key in ("tile", "steel", "world", "sipm", "unknown")), "Birth volume partition fails")
    equal(b, event["tracks_started"], "Birth/start closure fails")
    equal(b, event["tracks_finalized"], "Birth/finalization closure fails")
    equal(b, sum(event["fate_"+fate] for fate in FATES), "Terminal fate closure fails")
    equal(event["generated_legacy"], scan["generated_optical_photons"], "Legacy G projection differs; G is not the all-birth denominator")
    equal(event["detected_legacy"], scan["sipm_detected_photons"], "Legacy D projection differs")
    equal(event["fate_sipm"], event["detected_legacy"], "Collected fate differs from full D")
    for medium in ("tile", "steel"):
        close(event[f"{medium}_nonoptical_edep_mev"]+event[f"{medium}_optical_edep_mev"], event[f"legacy_{medium}_edep_mev"], f"{medium} energy decomposition fails")
        close(event[f"legacy_{medium}_edep_mev"], scan[f"{medium}_edep_mev"], f"{medium} legacy energy projection differs")
    count = config["sensors_per_layer"]
    require_fields(layer, ("run_id", "event_id", "layer", *BIRTH_FIELDS, *ENERGY_FIELDS, "generated_legacy", "scintillation_legacy", "cerenkov_legacy",
                           "detected_all_origins", "detected_same_root_layer", "detected_same_birth_layer"), "layer")
    require_fields(sensor, ("run_id", "event_id", "layer", "local_sensor", "global_copy", "detected_all_origins",
                            "detected_same_root_layer", "detected_same_birth_layer", "detected_root_outside", "detected_root_unknown",
                            "detected_birth_outside", "detected_birth_unknown"), "sensor")
    for context, values in (("layer", layer), ("sensor", sensor)):
        for key, value in values.items():
            if key.endswith("_mev"):
                require(np.isfinite(value).all() and (value >= -1e-12).all(), f"Invalid {context} energy {key}")
            elif key.endswith(("_x_mm", "_y_mm", "_z_mm")):
                continue
            else:
                values[key] = integers(value, f"{context}.{key}")
        require((values["run_id"] == event["run_id"][0]).all(), f"{context} run IDs differ")
    layer_keys = list(zip(layer["event_id"].tolist(), layer["layer"].tolist()))
    require(len(layer_keys) == events*10 and set(layer_keys) == {(e, l) for e in range(events) for l in range(10)}, "Duplicate/missing layer IDs")
    sensor_keys = list(zip(sensor["event_id"].tolist(), sensor["layer"].tolist(), sensor["local_sensor"].tolist()))
    require(len(sensor_keys) == events*10*count and set(sensor_keys) == {(e,l,s) for e in range(events) for l in range(10) for s in range(count)}, "Duplicate/missing sensor IDs")
    equal(sensor["global_copy"], 4*sensor["layer"]+sensor["local_sensor"], "Sensor global-copy mapping differs")
    require(np.isin(sensor["global_copy"], config["active_sensor_copies"]).all(), "Inactive sensor row")
    require((sensor["detected_root_unknown"] == 0).all() and (sensor["detected_birth_unknown"] == 0).all(), "Unknown sensor attribution")
    layer_order = np.lexsort((layer["layer"], layer["event_id"]))
    layer = {key: value[layer_order] for key, value in layer.items()}
    sensor_order = np.lexsort((sensor["local_sensor"], sensor["layer"], sensor["event_id"]))
    sensor = {key: value[sensor_order] for key, value in sensor.items()}
    equal(layer["generated_legacy"], layer["scintillation_legacy"]+layer["cerenkov_legacy"], "Layer legacy generation partition fails")
    layer_legacy = layer["generated_legacy"].reshape(events,10).sum(1)
    require((layer_legacy <= event["generated_legacy"]).all(), "Layer legacy G exceeds global legacy G")
    equal(layer["births_total"].reshape(events,10).sum(1), event["births_tile"], "Layer births differ from tile-born partition")
    for key in ENERGY_FIELDS:
        close(layer[key].reshape(events,10).sum(1), event[key], f"Layer sum differs for {key}")
    for key in ("detected_all_origins", "detected_same_root_layer", "detected_same_birth_layer"):
        equal(sensor[key].reshape(events,10,count).sum(2).ravel(), layer[key], f"Sensor/layer sum differs for {key}")
    equal(sensor["detected_all_origins"].reshape(events,10,count).sum((1,2)), event["detected_legacy"], "Sensor sum differs from full D")
    flow_fields = ("run_id", "event_id", "root_class", "root_layer", "root_sensor", "birth_class", "birth_layer", "birth_sensor",
                   "creator_process", "parent_optical", "fate", "sensor_copy", "photon_count")
    require_fields(flow, flow_fields, "flow")
    for key, value in flow.items():
        flow[key] = value.astype(str) if key == "creator_process" else integers(value, f"flow.{key}", minimum=None)
    require(not np.isin(flow["creator_process"], ["", "unassigned", "unmatched"]).any(), "Unresolved flow creator process")
    require(((flow["event_id"] >= 0)&(flow["event_id"] < events)).all(), "Invalid flow event IDs")
    require((flow["run_id"] == event["run_id"][0]).all() and (flow["photon_count"] > 0).all(), "Invalid flow run/count")
    keys = list(zip(*(flow[key].tolist() for key in flow_fields if key != "photon_count")))
    require(len(keys) == len(set(keys)), "Duplicate aggregated flow rows")
    require(np.isin(flow["fate"], range(12)).all() and np.isin(flow["parent_optical"], [0,1]).all(), "Invalid fate/parent flag")
    for prefix in ("birth", "root"):
        check_origin(flow, prefix, config)
    first_generation=flow["parent_optical"]==0
    for field in ("class","layer","sensor"):
        equal(flow["root_"+field][first_generation],flow["birth_"+field][first_generation],
              f"First-generation root/birth identity differs: {field}")
    collected = flow["fate"] == 0
    require(np.isin(flow["sensor_copy"][collected], config["active_sensor_copies"]).all() and
            (flow["sensor_copy"][~collected] == -1).all(), "Invalid terminal sensor identity")
    equal(sparse_sum(flow, np.ones(len(flow["event_id"]), dtype=bool), events), b, "Flow sum does not close total births")
    for code, fate in enumerate(FATES):
        equal(sparse_sum(flow, flow["fate"] == code, events), event["fate_"+fate], f"Flow fate differs: {fate}")
    primary = flow["creator_process"] == "primary"
    known_process = np.isin(flow["creator_process"], ["primary", "Scintillation", "Cerenkov", "OpWLS", "OpWLS2"])
    birth_masks = {"births_total": np.ones(len(primary), dtype=bool), "births_primary": primary,
                   "births_nonoptical_parent": ~primary & (flow["parent_optical"] == 0), "births_optical_parent": flow["parent_optical"] == 1,
                   "births_scintillation": flow["creator_process"] == "Scintillation", "births_cerenkov": flow["creator_process"] == "Cerenkov",
                   "births_wls": flow["creator_process"] == "OpWLS", "births_wls2": flow["creator_process"] == "OpWLS2", "births_other": ~known_process}
    for key, mask in birth_masks.items():
        equal(sparse_sum(flow, mask, events), event[key], f"Flow creator/parent partition differs: {key}")
        for index in range(10):
            equal(sparse_sum(flow, mask & (flow["birth_class"] == 1) & (flow["birth_layer"] == index), events),
                  layer[key].reshape(events,10)[:,index], f"Layer birth/flow mismatch: {key} layer {index}")
    for code, name in ((1,"tile"),(2,"steel"),(3,"world"),(4,"sipm")):
        equal(sparse_sum(flow, flow["birth_class"] == code, events), event["births_"+name], f"Flow birth volume differs: {name}")
    for index in range(10):
        for local in range(count):
            selected = collected & (flow["sensor_copy"] == 4*index+local)
            expected = {"detected_all_origins": selected,
                        "detected_same_root_layer": selected & (flow["root_class"] == 1) & (flow["root_layer"] == index),
                        "detected_same_birth_layer": selected & (flow["birth_class"] == 1) & (flow["birth_layer"] == index),
                        "detected_root_outside": selected & (flow["root_class"] != 1),
                        "detected_birth_outside": selected & (flow["birth_class"] != 1)}
            for key, mask in expected.items():
                equal(sparse_sum(flow, mask, events), sensor[key].reshape(events,10,count)[:,index,local], f"Sensor flow attribution differs: {key} {index}/{local}")
    return {"event_rows": events, "layer_rows": len(layer_keys), "sensor_rows": len(sensor_keys), "flow_rows": len(keys),
            "births_total": int(b.sum()), "generated_legacy": int(event["generated_legacy"].sum()),
            "legacy_global_minus_tile_generation": int((event["generated_legacy"]-layer_legacy).sum()),
            "detected_total": int(event["detected_legacy"].sum()), "births_optical_parent": int(event["births_optical_parent"].sum()),
            "fates": {fate: int(event["fate_"+fate].sum()) for fate in FATES}, "closure_passed": True}


def audit_run(run_dir: Path, config: dict, expected_events: int, *, accounting_enabled: bool=True,
              expected_seeds: tuple|None=None) -> dict:
    import csv
    run_dir = Path(run_dir).resolve()
    validate_configuration(config)
    require(isinstance(expected_events, int) and expected_events > 0, "Invalid expected event count")
    root_path = one((run_dir/"outputs").glob("*.root"), "event ROOT")
    macro = one((run_dir/"macros").glob("*.mac"), "macro")
    log = one((run_dir/"logs").glob("*.log"), "log")
    summary_path = one((run_dir/"outputs").glob("*_summary.csv"), "summary")
    state_path = root_path.with_suffix(".physics-state.json")
    runtime_path = root_path.with_suffix(".stack-v2-runtime.json")
    rng_path = root_path.with_suffix(".rng-end.txt")
    require(rng_path.is_file() and rng_path.stat().st_size > 0, "Missing RNG end state")
    log_report = check_log(log, expected_events)
    macro_report = check_macro(macro, config, accounting_enabled, expected_seeds)
    state = check_physics_state(state_path)
    runtime = check_runtime(runtime_path, config, accounting_enabled)
    saved = read_json(run_dir/"run_config.json")
    check_run_config(saved, config, accounting_enabled, expected_seeds)
    with uproot.open(root_path) as root:
        require("scan" in root, "Missing scan tree")
        scan = event_order(tree_arrays(root["scan"]), expected_events, "scan")
        for key in ("generated_optical_photons", "scintillation_photons", "cerenkov_photons", "sipm_detected_photons"):
            scan[key] = integers(scan[key], key)
        check_legacy_collection(scan["generated_optical_photons"],scan["sipm_detected_photons"],
            scan["collection_efficiency"],scan.get("collection_efficiency_valid"),"scan")
        check_empty_legacy_stack_trees(root)
        require((scan["generated_optical_photons"] >= scan["scintillation_photons"]+scan["cerenkov_photons"]).all(),
                "Legacy G is smaller than its scintillation/Cerenkov components")
        for axis, coordinate in zip("xyz", config["source"]["position_mm"]):
            close(scan[f"shoot_{axis}_mm"], coordinate, f"Source {axis} coordinate mismatch")
        close(scan["primary_kinetic_energy_mev"], config["source"]["kinetic_energy_mev"], "Source energy mismatch")
        with summary_path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        require(len(rows) == 1, "Summary is not one-row")
        summary = rows[0]
        require(int(summary["events"]) == expected_events and int(summary["committed_events"]) == expected_events, "Summary event mismatch")
        for key in ("generated_optical_photons", "scintillation_photons", "cerenkov_photons", "sipm_detected_photons"):
            require(int(summary[key]) == int(scan[key].sum()), f"Summary {key} differs")
        check_legacy_collection(int(summary["generated_optical_photons"]),int(summary["sipm_detected_photons"]),
            float(summary["collection_efficiency"]),int(summary["collection_efficiency_valid"]),"summary")
        if accounting_enabled:
            require(all(name in root for name in TREES), "Missing v2 accounting tree")
            ledgers = check_ledgers(scan, *(tree_arrays(root[name]) for name in TREES), config, expected_events)
        else:
            require(not any(name in root for name in TREES), "Accounting-off run contains v2 trees")
            ledgers = None
    files = {str(path.relative_to(run_dir)): {"sha256": sha256(path), "bytes": path.stat().st_size}
             for path in (root_path, macro, log, summary_path, state_path, runtime_path, rng_path, run_dir/"run_config.json")}
    return {"schema_version": "steel-stack-v2-run-audit-v1", "passed": True, "run_dir": str(run_dir),
            "configuration_hash": configuration_hash(config), "events": expected_events,
            "accounting_enabled": accounting_enabled, "physics_state": state, "runtime": runtime, "log": log_report,
            "macro": macro_report, "ledgers": ledgers, "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--config-json", required=True, type=Path)
    parser.add_argument("--events", required=True, type=int)
    parser.add_argument("--accounting", required=True, choices=("on", "off"))
    parser.add_argument("--seed1", type=int); parser.add_argument("--seed2", type=int)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args()
    try:
        require((args.seed1 is None) == (args.seed2 is None), "Both seeds are required together")
        result = audit_run(args.run_dir, read_json(args.config_json), args.events,
                           accounting_enabled=args.accounting == "on",
                           expected_seeds=None if args.seed1 is None else (args.seed1,args.seed2))
    except Exception as error:
        result = {"schema_version": "steel-stack-v2-run-audit-v1", "passed": False, "errors": [f"{type(error).__name__}: {error}"]}
    args.output_json.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"passed": result["passed"], "output": str(args.output_json), "errors": result.get("errors", [])}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
