import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit
import model


def ledger_fixture(events=2, zero=False, outside=False):
    config = model.make_configuration("back-center", 4, .5)
    event_names = {"run_id", "event_id", "layers", "sensors_per_layer", "sensor_copy_stride", "generated_legacy", "detected_legacy",
                   *audit.BIRTH_FIELDS, *audit.HARD_ZERO, "tracks_started", "tracks_finalized", "primary_optical_started",
                   "births_tile", "births_steel", "births_world", "births_sipm", *["fate_"+name for name in audit.FATES]}
    event = {key:np.zeros(events,dtype=np.int64) for key in event_names}
    event.update({key:np.zeros(events) for key in audit.ENERGY_FIELDS})
    event["event_id"] = np.arange(events); event["layers"][:] = 10; event["sensors_per_layer"][:] = 1; event["sensor_copy_stride"][:] = 4
    layer_names = {"run_id", "event_id", "layer", *audit.BIRTH_FIELDS, "generated_legacy", "scintillation_legacy", "cerenkov_legacy",
                   "detected_all_origins", "detected_same_root_layer", "detected_same_birth_layer"}
    layer = {key:np.zeros(events*10,dtype=np.int64) for key in layer_names}
    layer.update({key:np.zeros(events*10) for key in audit.ENERGY_FIELDS})
    layer["event_id"]=np.repeat(np.arange(events),10); layer["layer"]=np.tile(np.arange(10),events)
    sensor_names = {"run_id","event_id","layer","local_sensor","global_copy","detected_all_origins","detected_same_root_layer",
                    "detected_same_birth_layer","detected_root_outside","detected_root_unknown","detected_birth_outside","detected_birth_unknown"}
    sensor = {key:np.zeros(events*10,dtype=np.int64) for key in sensor_names}
    sensor["event_id"]=layer["event_id"].copy(); sensor["layer"]=layer["layer"].copy(); sensor["global_copy"]=4*sensor["layer"]
    flow_rows=[]
    if not zero:
        for key,value in {"generated_legacy":8,"detected_legacy":3,"births_total":10,"births_nonoptical_parent":8,
                           "births_optical_parent":2,"births_scintillation":10,"births_tile":9 if outside else 10,
                           "births_world":1 if outside else 0,"tracks_started":10,"tracks_finalized":10,
                           "fate_sipm":3,"fate_bulk_tile":6 if outside else 7,"fate_bulk_world":1 if outside else 0}.items():event[key][:]=value
        event["tile_nonoptical_edep_mev"][:]=1.;event["tile_optical_edep_mev"][:]=.00002;event["legacy_tile_edep_mev"][:]=1.00002
        for e in range(events):
            ix=e*10
            for key,value in {"births_total":9 if outside else 10,"births_nonoptical_parent":7 if outside else 8,
                              "births_optical_parent":2,"births_scintillation":9 if outside else 10,"generated_legacy":7 if outside else 8,
                              "scintillation_legacy":7 if outside else 8,"detected_all_origins":3,"detected_same_root_layer":3,"detected_same_birth_layer":3}.items():layer[key][ix]=value
            for key in audit.ENERGY_FIELDS:layer[key][ix]=event[key][e]
            for key in ("detected_all_origins","detected_same_root_layer","detected_same_birth_layer"):sensor[key][ix]=3
            for parent,fate,count in ((0,0,2),(0,1,5 if outside else 6),(1,0,1),(1,1,1)):
                flow_rows.append([0,e,1,0,-1,1,0,-1,"Scintillation",parent,fate,0 if fate==0 else -1,count])
            if outside:flow_rows.append([0,e,3,-1,-1,3,-1,-1,"Scintillation",0,3,-1,1])
    names=("run_id","event_id","root_class","root_layer","root_sensor","birth_class","birth_layer","birth_sensor",
           "creator_process","parent_optical","fate","sensor_copy","photon_count")
    flow={key:np.array([row[i] for row in flow_rows],dtype="U32" if key=="creator_process" else np.int64) for i,key in enumerate(names)}
    scan={"event_id":np.arange(events),"generated_optical_photons":event["generated_legacy"].copy(),
          "scintillation_photons":event["generated_legacy"].copy(),"cerenkov_photons":np.zeros(events,dtype=np.int64),
          "sipm_detected_photons":event["detected_legacy"].copy(),"tile_edep_mev":event["legacy_tile_edep_mev"].copy(),
          "steel_edep_mev":event["legacy_steel_edep_mev"].copy()}
    return scan,event,layer,sensor,flow,config


class ModelTests(unittest.TestCase):
    def test_explicit_matrices_and_nine_gaps(self):
        self.assertEqual(len(model.make_matrix("sensitivity",[.5,1.])),8)
        self.assertEqual(len(model.make_matrix("full",[.5,1.])),48)
        config=model.make_configuration("back-two",4,.5)
        self.assertEqual(config["core_length_mm"],444.5)
        self.assertEqual(config["active_sensor_copies"][:4],[0,1,4,5])
        with self.assertRaises(TypeError):model.make_configuration("back-center",4)
        for gap in (0.,.49,float("nan")):
            with self.assertRaises(ValueError):model.make_configuration("back-center",4,gap)

    def test_budget_seeds_and_explicit_repeat(self):
        configs=model.make_matrix("sensitivity",[.5,1.])
        kwargs=dict(events_per_task=5,blocks=2,campaign_seed=123,total_event_budget=80)
        tasks=model.prepare_tasks(configs,**kwargs)
        self.assertEqual(tasks,model.prepare_tasks(configs,**kwargs))
        self.assertEqual(model.validate_tasks(tasks)["unique_independent_seeds"],32)
        repeated=model.repeat_task(tasks[0],"repeat-test")
        model.validate_tasks(tasks+[repeated],total_event_budget=85)
        with self.assertRaises(ValueError):model.prepare_tasks(configs,**dict(kwargs,total_event_budget=79))
        bad=copy.deepcopy(tasks);bad[1]["seed1"]=bad[0]["seed1"]
        with self.assertRaises(ValueError):model.validate_tasks(bad)


class LedgerTests(unittest.TestCase):
    def test_zero_photons_is_valid(self):
        result=audit.check_ledgers(*ledger_fixture(zero=True),2)
        self.assertEqual(result["births_total"],0)

    def test_rescintillation_not_forced_equal_to_legacy_g(self):
        result=audit.check_ledgers(*ledger_fixture(),2)
        self.assertEqual(result["births_total"],20)
        self.assertEqual(result["generated_legacy"],16)

    def test_known_outside_births_are_valid(self):
        result=audit.check_ledgers(*ledger_fixture(outside=True),2)
        self.assertEqual(result["legacy_global_minus_tile_generation"],2)

    def test_duplicate_layer_and_unknown_origin_fail(self):
        data=list(ledger_fixture());data[2]["layer"][1]=0
        with self.assertRaisesRegex(ValueError,"Duplicate/missing layer"):audit.check_ledgers(*data,2)
        data=list(ledger_fixture());data[4]["root_class"][0]=0
        with self.assertRaisesRegex(ValueError,"Unknown root"):audit.check_ledgers(*data,2)

    def test_ledger_mismatch_and_duplicate_flow_fail(self):
        data=list(ledger_fixture());data[1]["tracks_finalized"][0]-=1
        with self.assertRaisesRegex(ValueError,"finalization"):audit.check_ledgers(*data,2)
        data=list(ledger_fixture());data[4]={key:np.concatenate([value,value[:1]]) for key,value in data[4].items()}
        with self.assertRaisesRegex(ValueError,"Duplicate aggregated flow"):audit.check_ledgers(*data,2)

    def test_no_rindex_on_collected_step_cannot_hide_in_collected_fate(self):
        data=list(ledger_fixture());data[1]["collected_step_no_rindex"][0]=1
        with self.assertRaisesRegex(ValueError,"collected_step_no_rindex"):audit.check_ledgers(*data,2)

    def test_first_generation_must_keep_its_actual_birth_origin(self):
        data=list(ledger_fixture());data[4]["root_layer"][1]=1  # legal ID, but wrong first-generation origin
        with self.assertRaisesRegex(ValueError,"First-generation root/birth"):audit.check_ledgers(*data,2)
        data=list(ledger_fixture());data[4]["root_layer"][3]=1  # optical descendant may inherit another tile's origin
        self.assertTrue(audit.check_ledgers(*data,2)["closure_passed"])

    def test_legacy_ce_is_checked_without_a_d_less_than_legacy_g_assumption(self):
        audit.check_legacy_collection([2,0],[3,1],[1.5,np.nan],[1,0])
        with self.assertRaisesRegex(ValueError,"zero legacy G"):audit.check_legacy_collection([0],[0],[0],[0])
        with self.assertRaisesRegex(ValueError,"ratio differs"):audit.check_legacy_collection([2],[3],[.5],[1])
        with self.assertRaisesRegex(ValueError,"validity flag"):audit.check_legacy_collection([2],[3],[1.5],[0])
        audit.check_legacy_collection([2,0],[3,0],[1.5,np.nan])  # older scan schema without validity column

    def test_v2_legacy_stack_trees_must_be_empty(self):
        audit.check_empty_legacy_stack_trees({})
        audit.check_empty_legacy_stack_trees({"stack_layers":SimpleNamespace(num_entries=0)})
        with self.assertRaisesRegex(ValueError,"populated legacy tree"):
            audit.check_empty_legacy_stack_trees({"stack_transfers":SimpleNamespace(num_entries=1)})

    def test_missing_file_and_changed_hash_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"data"
            with self.assertRaisesRegex(ValueError,"Missing file"):audit.verify_file_hash(path,"0"*64)
            path.write_bytes(b"evidence")
            with self.assertRaisesRegex(ValueError,"hash mismatch"):audit.verify_file_hash(path,"0"*64)

    def test_serial_warning_allowlist_is_narrow(self):
        header="Geant4 version Name: geant4-11-04-patch-02\nNumber of events: 2\n... close file : out.root - done\n"
        warning="-------- WWWW ------- G4Exception-START -------- WWWW -------\n*** G4Exception : Analysis_W001\n      issued by : G4RootNtupleFileManager::SetNtupleMergingMode\nMerging ntuples is not applicable in sequential application.\nSetting was ignored.\n*** This is just a warning message. ***\n-------- WWWW ------- G4Exception-END -------- WWWW -------\n"
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"log";path.write_text(header+warning)
            self.assertEqual(audit.check_log(path,2)["serial_ntuple_warning_count"],1)
            path.write_text(header+warning.replace("SetNtupleMergingMode","DifferentMethod"))
            with self.assertRaises(ValueError):audit.check_log(path,2)


if __name__ == "__main__":unittest.main()
