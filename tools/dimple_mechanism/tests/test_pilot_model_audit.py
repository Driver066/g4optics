"""Offline 100-row ROOT fixtures; no Geant4 process is launched by these tests."""
import copy
import csv
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import uproot

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import model
import pilot_model
import pilot_audit
from test_engineering import flat_geometry_log

TEMPLATE = (TOOLS.parents[1]/pilot_model.TEMPLATE).read_text()


def task_at(x, y):
    return next(task for task in pilot_model.tasks() if task["key"] == "A" and task["block"] == 0
                and task["x_mm"] == x and task["y_mm"] == y)


def fixture(task, no_detection=False, no_generation=False):
    n = 100
    generated = np.array([10]*50+[0]*50, dtype=np.int32)
    if no_generation:
        generated[:] = 0
    valid = generated > 0
    detected = np.where(valid, 2, 0).astype(np.int32)
    if no_detection or no_generation:
        detected[:] = 0
    ce = np.full(n, np.nan)
    np.divide(detected, generated, out=ce, where=valid)
    zero = np.zeros(n, dtype=np.int32)
    scan = dict(event_id=np.arange(n, dtype=np.int32), generated_optical_photons=generated,
                scintillation_photons=generated.copy(), cerenkov_photons=zero.copy(),
                sipm_detected_photons=detected, collection_efficiency=ce,
                collection_efficiency_valid=valid.astype(np.int32), scint_centroid_valid=valid.astype(np.int32),
                primary_kinetic_energy_mev=np.ones(n), steel_edep_mev=np.zeros(n),
                tile_edep_mev=np.where(valid, .3, 0.), electron_tile_edep_mev=np.where(valid, .25, 0.))
    for axis, value in (("x",task["x_mm"]),("y",task["y_mm"]),("z",18.)):
        scan[f"shoot_{axis}_mm"] = np.full(n,value,dtype=float)
        scan[f"scint_centroid_{axis}_mm"] = np.where(valid,11. if axis=="z" else value,np.nan)
    for i in range(4):
        scan[f"sipm_sensor_{i}_detected_photons"] = detected.copy() if i==0 else zero.copy()
    diagonal_zero_fields = ("event_aborted", "nonoptical_parent_cerenkov_births", "optical_parent_cerenkov_births",
                           "nonoptical_parent_other_births", "optical_parent_other_births", "optical_primary_tracks",
                           "duplicate_sipm_detections", "unregistered_sipm_detections", "optical_tracking_resumes")
    diag = {name: zero.copy() for name in diagonal_zero_fields}
    optical_children = np.where(valid,2,0).astype(np.int32)
    diag.update(event_id=np.arange(n,dtype=np.int32), legacy_generated=generated.copy(), legacy_detected=detected.copy(),
                nonoptical_parent_scintillation_births=generated.copy(), optical_parent_scintillation_births=optical_children,
                all_optical_births=generated+optical_children, optical_tracks_started=generated+optical_children,
                sipm_detection_calls=detected.copy(), sipm_unique_detected_tracks=detected.copy(),
                primary_count=np.ones(n,dtype=np.int32), primary_pdg=np.full(n,11,dtype=np.int32),
                primary_dir_x=np.zeros(n),primary_dir_y=np.zeros(n),primary_dir_z=-np.ones(n),
                primary_kinetic_energy_mev=np.ones(n))
    g = 0 if no_generation else 500
    d = 0 if no_detection or no_generation else 100
    summary = dict(events=n,committed_events=n,shoot_position_events=n,primary_energy_events=n,
                   generated_optical_photons=g,scintillation_photons=g,cerenkov_photons=0,sipm_detected_photons=d,
                   collection_efficiency_valid=int(g>0),collection_efficiency=d/g if g else np.nan,
                   production_scint_photons_per_event=g/n,net_sipm_photons_per_event=d/n,
                   steel_edep_sum_mev=0.,tile_edep_sum_mev=15. if g else 0.,
                   primary_energy_mean_mev=1.,primary_energy_rms_mev=0.,primary_energy_min_mev=1.,primary_energy_max_mev=1.,
                   shoot_x_mm=task["x_mm"],shoot_y_mm=task["y_mm"],shoot_z_mm=18.)
    for prefix, mean in (("generated_optical",g/n),("scintillation",g/n),("sipm_detected",d/n)):
        summary.update({f"{prefix}_mean":mean,f"{prefix}_rms":mean,f"{prefix}_se":mean/10,
                        f"{prefix}_zero_events":50 if mean else 100,f"{prefix}_zero_fraction":.5 if mean else 1.})
    return scan,diag,summary


def write_fixture(directory, task, scan=None, diag=None, summary=None, marker="ISSUE9_PILOT_DONE\n"):
    initial = fixture(task)
    scan,diag,summary = (initial[i] if value is None else value for i,value in enumerate((scan,diag,summary)))
    with uproot.recreate(directory/"result.root") as root:
        root.mktree("scan",{name:value.dtype for name,value in scan.items()})
        root["scan"].extend(scan)
        root.mktree(pilot_audit.legacy.DIAGNOSTIC_TREE,{name:value.dtype for name,value in diag.items()})
        root[pilot_audit.legacy.DIAGNOSTIC_TREE].extend(diag)
        for name in pilot_audit.legacy.EMPTY_TREES:
            root.mktree(name,{"event_id":np.dtype("int32")})
    with (directory/"result_summary.csv").open("w",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=summary)
        writer.writeheader(); writer.writerow(summary)
    (directory/"simulation.log").write_text(flat_geometry_log()+
        "Primary particle was: e- with energy 1 MeV.\nNumber of events: 100\n"
        "Material of world: G4_AIR\nMaterial of tank: G4_PLASTIC_SC_VINYLTOLUENE\n"+marker)
    (directory/"result.rng").write_text("MixMaxRng-begin 1\n12345\n67890\nMixMaxRng-end\n")


class PilotModelTests(unittest.TestCase):
    def test_exact_scope_and_independent_seed_partition(self):
        positions=pilot_model.positions(); tasks=pilot_model.tasks()
        self.assertEqual(len(positions),25)
        self.assertEqual(len({(p["x_mm"],p["y_mm"]) for p in positions}),25)
        self.assertEqual(len(tasks),350)
        self.assertEqual(sum(t["events"] for t in tasks),35000)
        self.assertEqual(len({t["task_id"] for t in tasks}),350)
        seeds=[seed for task in tasks for seed in task["seeds"]]
        self.assertEqual(seeds,list(range(2026102801,2026103501)))
        self.assertLess(max(seeds),2**31)
        self.assertEqual(len(set(seeds)),700)
        for key in "ABCDEFG":
            for block in (0,1):
                selected=[t for t in tasks if t["key"]==key and t["block"]==block]
                self.assertEqual([t["point_id"] for t in selected],[p["point_id"] for p in positions])
        self.assertEqual(sum(c["events"] for c in model.cases()),42)

    def test_negative_coordinates_and_nonzero_y_macro(self):
        for x,y in ((.8,.8),(-10.,-10.),(0.,-10.),(-10.,10.)):
            task=task_at(x,y); text=pilot_model.macro(TEMPLATE,task)
            self.assertIn(f"/gps/pos/centre {x:g} {y:g} 18 mm",text)
            self.assertIn("/run/beamOn 100\n",text)
            self.assertNotIn("/opnovice2/gun/randomDirection",text)
            self.assertEqual(text.count("/run/beamOn"),1)
            self.assertTrue(text.endswith("/control/echo ISSUE9_PILOT_DONE\n"))
            self.assertGreater(text.rfind("/random/setSeeds"),text.index("/geometry/test/run"))

    def test_tampered_scientific_inputs_and_template_fail(self):
        task=task_at(0.,0.)
        for key,value in (("events",2),("x_mm",3.),("y_mm",2.),("seeds",[1,2]),("block",True),
                          ("stage","smoke"),("overrides",{})):
            bad=copy.deepcopy(task); bad[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): pilot_model.macro(TEMPLATE,bad)
        bad=copy.deepcopy(task); bad["config"]["front"]=0
        with self.assertRaises(ValueError): pilot_model.validate_task(bad)
        with self.assertRaisesRegex(ValueError,"template differs"):
            pilot_model.macro(TEMPLATE.replace("1.58","1.1"),task)
        pilot_model.validate_task(dict(task,macro_sha256="a"*64,input_manifest_sha256="b"*64))


class PilotAuditTests(unittest.TestCase):
    def test_real_nonzero_y_negative_source_and_100_rows(self):
        for x,y in ((.8,.8),(-10.,-10.),(-10.,10.)):
            task=task_at(x,y); scan,diag,summary=fixture(task)
            before={key:values.copy() for key,values in scan.items()}
            with self.subTest(point=(x,y)),tempfile.TemporaryDirectory() as tmp:
                directory=Path(tmp); write_fixture(directory,task,scan,diag,summary)
                result=pilot_audit.audit_case(directory,task,0)
                self.assertTrue(result["accepted"])
                self.assertEqual(result["events"],100)
                self.assertEqual(result["zero_generated_events"],50)
                self.assertEqual(result["zero_response_events"],50)
                self.assertEqual(result["diagnostics"]["all_optical_births"],600)
                for key in scan: np.testing.assert_array_equal(scan[key],before[key])

    def test_zero_response_and_zero_denominator_are_retained(self):
        task=task_at(0.,-10.)
        for no_generation in (False,True):
            with self.subTest(no_generation=no_generation),tempfile.TemporaryDirectory() as tmp:
                scan,diag,summary=fixture(task,no_detection=True,no_generation=no_generation)
                write_fixture(Path(tmp),task,scan,diag,summary)
                result=pilot_audit.audit_case(Path(tmp),task,0)
                self.assertEqual(result["detected_photons"],0)
                self.assertEqual(result["zero_response_events"],100)
                self.assertEqual(result["collection_efficiency"],None if no_generation else 0.)

    def test_actual_y_z_ids_counts_and_centroid_corruption_fail(self):
        task=task_at(-10.,10.)
        mutations=(("shoot_y_mm",0.,"shoot_y_mm"),("shoot_x_mm",10.,"shoot_x_mm"),
                   ("shoot_z_mm",4.,"shoot_z_mm"),("event_id",99,"event IDs"),
                   ("sipm_sensor_1_detected_photons",1,"sum of four"),
                   ("scint_centroid_z_mm",np.nan,"centroid"),("electron_tile_edep_mev",-1.,"energy"))
        for key,value,reason in mutations:
            scan,_,_=fixture(task); scan[key][0]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,reason): pilot_audit.audit_scan(scan,task)
        scan,_,_=fixture(task)
        with self.assertRaisesRegex(ValueError,"row count"):
            pilot_audit.audit_scan({k:v[:-1] for k,v in scan.items()},task)

    def test_summary_checks_original_y_not_projected_zero(self):
        task=task_at(-10.,10.); scan,diag,summary=fixture(task); summary["shoot_y_mm"]=0.
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(Path(tmp),task,scan,diag,summary)
            with self.assertRaisesRegex(ValueError,"summary shoot_y_mm"):
                pilot_audit.audit_case(Path(tmp),task,0)

    def test_completion_newline_duplicates_and_rng_integrity(self):
        task=task_at(.8,.8)
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); write_fixture(directory,task,marker="ISSUE9_PILOT_DONE\r\n")
            self.assertTrue(pilot_audit.audit_case(directory,task,0)["accepted"])
            write_fixture(directory,task,marker="ISSUE9_PILOT_DONE\nISSUE9_PILOT_DONE\n")
            with self.assertRaisesRegex(ValueError,"duplicate"): pilot_audit.audit_case(directory,task,0)
            write_fixture(directory,task,marker="ISSUE9_SMOKE_DONE\n")
            with self.assertRaisesRegex(ValueError,"completion"): pilot_audit.audit_case(directory,task,0)
            write_fixture(directory,task)
            (directory/"result.rng").write_text("MixMaxRng-begin 1\n12345\n")
            with self.assertRaisesRegex(ValueError,"delimiters"): pilot_audit.audit_case(directory,task,0)

    def test_wrong_optical_birth_closure_and_process_exit_fail(self):
        task=task_at(-10.,-10.); scan,diag,summary=fixture(task); diag["optical_tracks_started"][0]-=2
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); write_fixture(directory,task,scan,diag,summary)
            with self.assertRaisesRegex(ValueError,"closure mismatch"): pilot_audit.audit_case(directory,task,0)
            with self.assertRaisesRegex(ValueError,"process exited"): pilot_audit.audit_case(directory,task,1)


if __name__ == "__main__": unittest.main()
