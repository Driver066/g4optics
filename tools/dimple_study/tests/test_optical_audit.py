"""Small independent ROOT fixtures exercise the engineering audit's failures."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np
import uproot
from uproot.writing.identify import to_TAxis, to_TH1x


SPEC = importlib.util.spec_from_file_location(
    "dimple_optical_audit", Path(__file__).resolve().parents[1] / "optical_audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)
CONFIG = {"events": 2, "x_mm": 20., "geometry": "flat", "diagnostics": False}
SERIAL_WARNING = """-------- WWWW ------- G4Exception-START -------- WWWW -------
*** G4Exception : Analysis_W001
      issued by : G4RootNtupleFileManager::SetNtupleMergingMode
Merging ntuples is not applicable in sequential application.
Setting was ignored.
*** This is just a warning message. ***
-------- WWWW ------- G4Exception-END -------- WWWW -------
"""
LOG = SERIAL_WARNING + """SiPM geometry: full_size=(6,6,0.5) mm
SiPM placement: layout=single, layer=0, local_sensor=0, copy=0, face=-Z, local=(0,0,0) mm, world=(0,0,-2.75) mm
Checking overlaps for volume Tank:0 (G4Box) ... OK!
Checking overlaps for volume SiPM:0 (G4Box) ... OK!
Geometry overlaps check completed !
Primary particle was: e- with energy 1 MeV.
Number of events: 2
Material of world: G4_AIR
Material of tank:  G4_PLASTIC_SC_VINYLTOLUENE
DIMPLE_OPTICAL_DONE
"""
RNG = b"MixMaxRng-begin 1\n906770732717044781\n17\n1977567618660788324\nMixMaxRng-end\n"


def fixture_scan():
    floats = {
        "shoot_x_mm": [20., 20.], "shoot_y_mm": [0., 0.], "shoot_z_mm": [4., 4.],
        "primary_kinetic_energy_mev": [1., 1.], "collection_efficiency": [.2, np.nan],
        "steel_edep_mev": [0., 0.], "tile_edep_mev": [.3, 0.],
    }
    ints = {
        "event_id": [0, 1], "generated_optical_photons": [10, 0],
        "scintillation_photons": [10, 0], "cerenkov_photons": [0, 0],
        "sipm_detected_photons": [2, 0], "collection_efficiency_valid": [1, 0],
        "sipm_sensor_0_detected_photons": [2, 0],
        "sipm_sensor_1_detected_photons": [0, 0],
        "sipm_sensor_2_detected_photons": [0, 0],
        "sipm_sensor_3_detected_photons": [0, 0],
    }
    return {**{name: np.array(values, dtype=np.float64) for name, values in floats.items()},
            **{name: np.array(values, dtype=np.int32) for name, values in ints.items()}}


def fixture_diag():
    zero = ("event_aborted", "nonoptical_parent_cerenkov_births", "optical_parent_cerenkov_births",
            "nonoptical_parent_other_births", "optical_parent_other_births", "optical_primary_tracks",
            "duplicate_sipm_detections", "unregistered_sipm_detections")
    integers = {name: [0, 0] for name in zero}
    integers.update(event_id=[0, 1], legacy_generated=[10, 0], legacy_detected=[2, 0],
                    nonoptical_parent_scintillation_births=[10, 0],
                    optical_parent_scintillation_births=[4, 0], all_optical_births=[14, 0],
                    optical_tracks_started=[14, 0], optical_tracking_resumes=[1, 0],
                    sipm_detection_calls=[2, 0], sipm_unique_detected_tracks=[2, 0],
                    primary_count=[1, 1], primary_pdg=[11, 11])
    floats = {"primary_dir_x": [0., 0.], "primary_dir_y": [0., 0.],
              "primary_dir_z": [-1., -1.], "primary_kinetic_energy_mev": [1., 1.]}
    return {**{name: np.array(values, dtype=np.int32) for name, values in integers.items()},
            **{name: np.array(values, dtype=np.float64) for name, values in floats.items()}}


def fixture_summary():
    summary = {
        "events": 2, "committed_events": 2, "shoot_position_events": 2, "primary_energy_events": 2,
        "generated_optical_photons": 10, "scintillation_photons": 10, "cerenkov_photons": 0,
        "sipm_detected_photons": 2, "collection_efficiency_valid": 1, "collection_efficiency": .2,
        "production_scint_photons_per_event": 5., "net_sipm_photons_per_event": 1.,
        "steel_edep_sum_mev": 0., "tile_edep_sum_mev": .3,
        "primary_energy_mean_mev": 1., "primary_energy_rms_mev": 0.,
        "primary_energy_min_mev": 1., "primary_energy_max_mev": 1.,
        "shoot_x_mm": 20., "shoot_y_mm": 0., "shoot_z_mm": 4.,
    }
    for prefix, mean in (("generated_optical", 5.), ("scintillation", 5.), ("sipm_detected", 1.)):
        summary.update({f"{prefix}_mean": mean, f"{prefix}_rms": mean,
                        f"{prefix}_se": mean / np.sqrt(2),
                        f"{prefix}_zero_events": 1, f"{prefix}_zero_fraction": .5})
    return summary


def write_fixture(directory, scan=None, diag=None, log=LOG, histogram_flow=1., histogram_variance=1.):
    directory.mkdir(parents=True, exist_ok=True)
    scan = fixture_scan() if scan is None else scan
    with uproot.recreate(directory / "result.root") as root:
        root.mktree("scan", {name: value.dtype for name, value in scan.items()})
        root["scan"].extend(scan)
        for name in ("decay_betas", "stack_layers", "stack_transfers", "layout_layers", "layout_sensors", "layout_transfers"):
            root.mktree(name, {"event_id": np.dtype("int32")})
        if diag is not None:
            root.mktree("dimple_optical_event_v1", {name: value.dtype for name, value in diag.items()})
            root["dimple_optical_event_v1"].extend(diag)
        root["response"] = to_TH1x(
            "response", "synthetic response", np.array([histogram_flow, 2., 3., 4.]),
            10., 10., 10., 13., 19., np.array([histogram_variance, 2., 3., 4.]),
            to_TAxis("xaxis", "", 2, 0., 2.))
    with (directory / "result_summary.csv").open("w", newline="") as handle:
        summary = fixture_summary()
        writer = csv.DictWriter(handle, fieldnames=summary)
        writer.writeheader()
        writer.writerow(summary)
    (directory / "simulation.log").write_text(log)
    (directory / "result.rng").write_bytes(RNG)


class OpticalAuditTests(unittest.TestCase):
    def test_valid_off_and_on_including_zero_light_and_resumed_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_fixture(directory)
            self.assertEqual(audit.audit_case(directory, CONFIG, 0)["detected_photons"], 2)
            write_fixture(directory, diag=fixture_diag())
            report = audit.audit_case(directory, {**CONFIG, "diagnostics": True}, 0)
            self.assertEqual(report["diagnostics"]["optical_parent_scintillation_births"], 4)
            self.assertEqual(report["diagnostics"]["optical_tracking_resumes"], 1)

    def test_rejects_corrupt_scan_and_summary(self):
        cases = (("event_id", [0, 0], "event IDs"),
                 ("shoot_z_mm", [4., 2.5], "wrong source"),
                 ("generated_optical_photons", [-10, 0], "negative"),
                 ("cerenkov_photons", [1, 0], "G !="),
                 ("sipm_sensor_1_detected_photons", [1, 0], "sum of four"),
                 ("collection_efficiency", [.4, np.nan], "D/G"))
        with tempfile.TemporaryDirectory() as tmp:
            for name, values, error in cases:
                with self.subTest(name=name):
                    scan = fixture_scan()
                    scan[name][:] = values
                    write_fixture(Path(tmp), scan=scan)
                    with self.assertRaisesRegex(ValueError, error):
                        audit.audit_case(Path(tmp), CONFIG, 0)
            write_fixture(Path(tmp))
            path = Path(tmp) / "result_summary.csv"
            path.write_text(path.read_text().replace("10,10,0,2,1,0.2", "11,10,0,2,1,0.2"))
            with self.assertRaisesRegex(ValueError, "summary generated"):
                audit.audit_case(Path(tmp), CONFIG, 0)

    def test_rejects_wrong_geometry_and_unknown_serial_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            for log, error in ((LOG.replace("full_size=(6,6,0.5)", "full_size=(2,2,0.5)"), "geometry"),
                               (LOG.replace("Geometry overlaps check completed !", ""), "incomplete"),
                               (LOG.replace("DIMPLE_OPTICAL_DONE", ""), "completion marker"),
                               (LOG.replace("Setting was ignored.", "Unexpected data loss."), "G4Exception")):
                write_fixture(Path(tmp), log=log)
                with self.assertRaisesRegex(ValueError, error):
                    audit.audit_case(Path(tmp), CONFIG, 0)

    def test_rejects_diagnostic_closure_and_source_errors(self):
        for name, value, error in (("optical_tracks_started", 13, "closure"),
                                   ("sipm_unique_detected_tracks", 1, "closure"),
                                   ("duplicate_sipm_detections", 1, "nonzero"),
                                   ("primary_dir_z", 1., "source")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                diag = fixture_diag()
                diag[name][0] = value
                write_fixture(Path(tmp), diag=diag)
                with self.assertRaisesRegex(ValueError, error):
                    audit.audit_case(Path(tmp), {**CONFIG, "diagnostics": True}, 0)
        # Keep every legacy/detection equality valid, but report more uniquely
        # detected tracks than the independent birth/start ledger contains.
        scan, diag = fixture_scan(), fixture_diag()
        scan["sipm_detected_photons"][0] = 15
        scan["sipm_sensor_0_detected_photons"][0] = 15
        scan["collection_efficiency"][0] = 1.5
        for name in ("legacy_detected", "sipm_detection_calls", "sipm_unique_detected_tracks"):
            diag[name][0] = 15
        with tempfile.TemporaryDirectory() as tmp:
            write_fixture(Path(tmp), scan=scan, diag=diag)
            with self.assertRaisesRegex(ValueError, "exceed all optical births"):
                audit.audit_case(Path(tmp), {**CONFIG, "diagnostics": True}, 0)

    def test_legacy_comparison_checks_branches_flow_errors_csv_and_rng(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref, candidate = Path(tmp) / "ref", Path(tmp) / "candidate"
            write_fixture(ref)
            write_fixture(candidate, diag=fixture_diag())
            self.assertEqual(audit.compare_legacy(ref, candidate)["histograms_compared"], 1)
            changes = (({"histogram_flow": 2.}, "bins/flow"),
                       ({"histogram_variance": 2.}, "errors/flow"))
            for kwargs, error in changes:
                with self.subTest(error=error):
                    write_fixture(candidate, **kwargs)
                    with self.assertRaisesRegex(ValueError, error):
                        audit.compare_legacy(ref, candidate)
            write_fixture(candidate)
            (candidate / "result.rng").write_bytes(RNG.replace(b"1977567618660788324", b"1977567618660788325"))
            with self.assertRaisesRegex(ValueError, "RNG state bytes"):
                audit.compare_legacy(ref, candidate)
            scan = fixture_scan()
            scan["shoot_x_mm"][0] = 21.
            write_fixture(candidate, scan=scan)
            with self.assertRaisesRegex(ValueError, "legacy tree branch"):
                audit.compare_legacy(ref, candidate)
            write_fixture(candidate)
            with (candidate / "result_summary.csv").open("ab") as handle:
                handle.write(b"\n")
            with self.assertRaisesRegex(ValueError, "summary bytes"):
                audit.compare_legacy(ref, candidate)

    def test_rng_required_for_candidates_and_explicit_comparison_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref, candidate = Path(tmp) / "ref", Path(tmp) / "candidate"
            write_fixture(ref)
            write_fixture(candidate)
            (ref / "result.rng").unlink()
            self.assertEqual(audit.audit_case(ref, {**CONFIG, "variant": "reference"}, 0)["status"], "passed")
            with self.assertRaisesRegex(ValueError, "missing RNG"):
                audit.audit_case(ref, CONFIG, 0)
            report = audit.compare_legacy(ref, candidate, require_rng=False)
            self.assertFalse(report["rng_compared"])
            self.assertNotIn("rng_state_bytes_equal", report)
            with self.assertRaisesRegex(ValueError, "missing RNG"):
                audit.compare_legacy(ref, candidate)
            (candidate / "result.rng").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "empty RNG"):
                audit.audit_case(candidate, CONFIG, 0)


if __name__ == "__main__":
    unittest.main()
