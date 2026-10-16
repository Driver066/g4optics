"""Behavioral and failure-injection checks; no Geant4 process or Docker execution."""
from __future__ import annotations

import copy
import contextlib
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import uproot

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))
from common import CASES, SOURCE_DIRECTORY, SPECTRUM, TRANSMISSION, absorption_vector, case_geometry, command_lines, expected_seeds
from prepare import make_macro, prepare
from audit import audit_tables, audit_readout, compare_baseline_files, validate_inputs
from analyze import bootstrap_difference
import run as runner

REPO = TOOL_DIR.parents[1]


def fixture(combined=True):
    """Independent observations include a zero event and D>legacyG from extra births."""
    a = {key: np.array(value) for key, value in {
        'event_id': [0, 1], 'primary_kinetic_energy_mev': [0.3, 0.5],
        'shoot_x_mm': [0., 0.], 'shoot_y_mm': [0., 0.],
        'shoot_z_mm': [9.55 if combined else 9.5] * 2,
        'hit_valid': [1, 0], 'hit_z_mm': [8.05 if combined else 8., np.nan],
        'generated_optical_photons': [4, 0], 'scintillation_photons': [4, 0],
        'cerenkov_photons': [0, 0], 'sipm_detected_photons': [5, 0],
        'sipm_sensor_0_detected_photons': [3, 0], 'sipm_sensor_1_detected_photons': [2, 0],
        'sipm_sensor_2_detected_photons': [0, 0], 'sipm_sensor_3_detected_photons': [0, 0],
        'collection_efficiency_valid': [1, 0], 'collection_efficiency': [1.25, np.nan],
        'tile_edep_mev': [.3, 0.], 'electron_tile_edep_mev': [.3, 0.],
        'proton_tile_edep_mev': [0., 0.], 'other_charged_tile_edep_mev': [0., 0.],
        'neutral_tile_edep_mev': [0., 0.], 'steel_edep_mev': [0., 0.],
    }.items()}
    c = {'event_id': np.array([0, 1])}
    for key, value in {
        'front_generated': 3, 'back_generated': 1,
        'lineage_front_to_sensor0': 3, 'lineage_front_to_sensor1': 0,
        'lineage_back_to_sensor0': 0, 'lineage_back_to_sensor1': 2,
        'unknown_to_sensor0': 0, 'unknown_to_sensor1': 0,
        'all_births_front': 4, 'all_births_back': 2, 'all_births_joint': 0, 'all_births_other': 0,
        'optical_parent_births': 2, 'boundary_steps': 8, 'joint_boundary_steps': 3, 'no_rindex_steps': 0,
        'front_edep_mev': .1, 'back_edep_mev': .2, 'joint_edep_mev': .01,
        'front_optical_edep_mev': .01, 'back_optical_edep_mev': .02, 'joint_optical_edep_mev': .001,
    }.items():
        c[key] = np.array([value, 0])
    b = {key: np.array(value) for key, value in {
        'event_id': [0, 0, 0, 0], 'pre_region': [0, 2, 1, 0], 'post_region': [2, 1, 4, 3],
        'status': [2, 2, 2, 2], 'count': [2, 1, 2, 3],
    }.items()}
    if not combined:
        c, b = {'event_id': np.array([], dtype=int)}, {'event_id': np.array([], dtype=int)}
    r = {key: np.array(value) for key, value in {
        'event_id': [0, 1], 'sensor0_entries': [3, 0], 'sensor1_entries': [2, 0],
        'sensor0_reflections': [1, 0], 'sensor1_reflections': [1, 0],
        'sensor0_step_too_small': [1, 0], 'sensor1_step_too_small': [1, 0],
        'no_rindex_steps': [0, 0]}.items()}
    return {'scan': a, 'combined_events': c, 'combined_boundaries': b, 'readout_events': r}


class MaterialTests(unittest.TestCase):
    def test_matches_existing_isolated_conversion_helper_byte_for_byte(self):
        runner = (REPO / SOURCE_DIRECTORY / 'run_sipm_cavity_scan.sh').read_text()
        helper = runner.split('effective_abs_length_vector_from_transmission_csv() {', 1)[1]
        code = helper.split("<<'PY'\n", 1)[1].split('\nPY\n}', 1)[0]
        result = subprocess.run([sys.executable, '-c', code, str(REPO / TRANSMISSION), '0.1'],
                                check=True, capture_output=True, text=True)
        self.assertEqual(absorption_vector(REPO / TRANSMISSION), result.stdout.strip())

    def test_beer_lambert_and_input_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'data.csv'
            path.write_text('wavelength_nm,transmission\n400,0.98\n500,0.98\n')
            parts = absorption_vector(path).split()
            self.assertAlmostEqual(float(parts[2]), -0.1 / np.log(.98), places=8)
            for body in ('400,1\n500,.9', '400,0\n500,.9', '400,.9\n400,.8', 'nan,.9\n500,.9'):
                path.write_text('wavelength_nm,transmission\n' + body + '\n')
                with self.subTest(body=body), self.assertRaises(ValueError):
                    absorption_vector(path)


class PreparationTests(unittest.TestCase):
    def test_physical_envelope_and_orientation(self):
        a, b = case_geometry(CASES[3]), case_geometry(CASES[4])
        self.assertEqual(a['source_z_mm'], 13.55)
        self.assertEqual(b['source_z_mm'], 13.55)
        self.assertEqual(a['joint_center_z_mm'], 4)
        self.assertEqual(b['joint_center_z_mm'], -4)
        self.assertAlmostEqual(a['front_center_z_mm'] - a['front_mm'] / 2, 4.05)

    def test_original_spectrum_exact_no_fixed_energy_zero_event(self):
        spectrum = (REPO / SPECTRUM).read_text()
        for raw in CASES:
            case = case_geometry(raw)
            text = make_macro(REPO, case, 0, expected_seeds(case, 'geometry', 0))
            commands = command_lines(text)
            with self.subTest(case=case['id']):
                self.assertEqual(text.count(spectrum), 1)
                self.assertFalse(any(line.startswith('/gps/energy ') or line.startswith('/run/beamOn') for line in commands))
                self.assertIn(f"/gps/pos/centre 0 0 {case['source_z_mm']:g} mm", commands)
                self.assertIn('/opnovice2/sipm/layout edge-two', commands)
                self.assertLess(text.index('/opnovice2/combined/enabled'), text.index('/run/initialize'))
                if case['combined']:
                    self.assertLess(text.index('/opnovice2/greaseProperty'), text.index('/run/initialize'))

    def test_readout_mode_is_explicit_and_legacy_cannot_be_scientific(self):
        case = case_geometry(CASES[0])
        for mode in ('boundary-entry', 'legacy'):
            macro = make_macro(REPO, case, 10, [11, 13], mode)
            self.assertIn('/opnovice2/sipm/readoutMode ' + mode, command_lines(macro))
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'not-created'
            with self.assertRaisesRegex(ValueError, 'exploration requires boundary-entry'):
                prepare(REPO, out, 'explore', readout_mode='legacy')
            self.assertFalse(out.exists())
            with self.assertRaisesRegex(ValueError, 'baseline regression requires legacy'):
                prepare(REPO, out, 'smoke', baseline=Path(temporary))
            self.assertFalse(out.exists())

    def test_fixed_budget_unique_streams_and_stale_input_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'campaign'
            m = prepare(REPO, out, 'explore')
            self.assertEqual(len(m['tasks']), 10)
            self.assertEqual(m['execution']['total_events'], 1000)
            self.assertEqual(len({tuple(t['seeds']) for t in m['tasks']}), 10)
            self.assertNotIn('tools/combined_tiles/probes.py', m['source_files_sha256'])
            validate_inputs(out, REPO)
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                prepare(REPO, out, 'explore')
            macro = out / m['tasks'][0]['id'] / 'run.mac'
            macro.write_text(macro.read_text().replace('/run/beamOn 100', '/run/beamOn 200'))
            with self.assertRaisesRegex(ValueError, 'macro hash'):
                validate_inputs(out, REPO)


class EventAuditTests(unittest.TestCase):
    def setUp(self):
        self.case = case_geometry(CASES[2])

    def test_zero_event_preserved_and_extra_births_not_false_failure(self):
        result = audit_tables(fixture(), self.case, 2)
        self.assertEqual(result['detected'], 5)
        self.assertEqual(result['zero_detected_events'], 1)
        self.assertEqual(result['zero_generated_events'], 1)
        self.assertEqual(result['D_per_event'], 2.5)
        self.assertAlmostEqual(result['diagnostics']['edep_mev']['front']['nonoptical'], .09)

    def test_failure_injections(self):
        mutations = [
            ('scan', 'event_id', [0, 0], 'event'),
            ('scan', 'shoot_z_mm', [9.5, 9.5], 'source'),
            ('scan', 'sipm_sensor_1_detected_photons', [1, 0], 'sensor'),
            ('scan', 'collection_efficiency', [1.25, 0.], 'NaN'),
            ('scan', 'tile_edep_mev', [.4, 0.], 'energy components'),
            ('combined_events', 'back_generated', [2, 0], 'generation'),
            ('combined_events', 'unknown_to_sensor0', [1, 0], 'transfer'),
            ('combined_events', 'no_rindex_steps', [1, 0], 'NoRINDEX'),
            ('combined_events', 'front_optical_edep_mev', [.2, 0.], 'exceeds'),
            ('combined_boundaries', 'count', [1, 1, 2, 3], 'boundary totals'),
            ('combined_boundaries', 'status', [14, 2, 2, 2], 'NoRINDEX'),
        ]
        for tree, key, values, message in mutations:
            data = fixture(); data[tree][key] = np.array(values)
            with self.subTest(tree=tree, key=key), self.assertRaisesRegex(ValueError, message):
                audit_tables(data, self.case, 2)

    def test_reflection_or_step_too_small_cannot_count_as_detection(self):
        for status in (3, 4, 13):
            data = fixture()
            data['combined_boundaries']['status'][-1] = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'true boundary-entry sensor 0'):
                audit_tables(data, self.case, 2)

    def test_monolith_requires_true_entry_observer_not_legacy_prestep(self):
        data = fixture(False)
        data['readout_events']['sensor0_entries'][0] = 0
        with self.assertRaisesRegex(ValueError, 'true entries sensor 0'):
            audit_tables(data, case_geometry(CASES[0]), 2)

    def test_sensor_to_sensor_candidate_is_not_incoming_entry(self):
        data = fixture()
        data['combined_boundaries']['pre_region'][-1] = 4
        with self.assertRaisesRegex(ValueError, 'true boundary-entry sensor 0'):
            audit_tables(data, self.case, 2)

    def test_legacy_observer_is_explicitly_empty_and_not_a_corrected_acceptance(self):
        data = fixture()
        with self.assertRaisesRegex(ValueError, 'legacy unexpectedly fills'):
            audit_tables(data, self.case, 2, 'legacy')
        data['readout_events'] = {'event_id': np.array([], dtype=int)}
        data['combined_boundaries']['status'][-1] = 3
        result = audit_tables(data, self.case, 2, 'legacy')
        self.assertEqual(result['readout']['mode'], 'legacy')

    def test_monolith_requires_empty_combined_tables(self):
        audit_tables(fixture(False), case_geometry(CASES[0]), 2)
        data = fixture(False); data['combined_events']['event_id'] = np.array([0])
        with self.assertRaisesRegex(ValueError, 'unexpectedly fills'):
            audit_tables(data, case_geometry(CASES[0]), 2)


class BootstrapTests(unittest.TestCase):
    def test_known_difference_percentage_and_constant_interval(self):
        result = bootstrap_difference([np.full(100, 100), np.full(100, 100)],
                                      [np.full(100, 80), np.full(100, 80)], seed=3)
        self.assertEqual(result['difference_D_per_event'], 20)
        self.assertEqual(result['percent_difference'], 25)
        self.assertEqual(result['difference_ci95'], [20, 20])
        self.assertEqual(result['percent_difference_ci95'], [25, 25])

    def test_zeros_retained_reproducible_and_no_undefined_percentage_hidden(self):
        args = ([np.array([0, 10]), np.array([0, 10])], [np.array([0, 0]), np.array([0, 0])])
        first = bootstrap_difference(*args, seed=8)
        self.assertEqual(first, bootstrap_difference(*args, seed=8))
        self.assertEqual(first['target_mean_D_per_event'], 5)
        self.assertIsNone(first['percent_difference'])
        self.assertIsNone(first['percent_difference_ci95'])
        self.assertEqual(first['undefined_percent_bootstrap_replicates'], 10000)

    def test_reject_undersized_bootstrap(self):
        with self.assertRaisesRegex(ValueError, '10000'):
            bootstrap_difference([np.ones(2)], [np.ones(2)], replicates=9999)


class BaselineRegressionTests(unittest.TestCase):
    def test_histogram_and_rng_mutations_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            old, new = Path(temporary) / 'old', Path(temporary) / 'new'
            for folder in (old, new):
                folder.mkdir()
                for name in ('result_summary.csv', 'currentEvent.rndm', 'currentRun.rndm'):
                    (folder / name).write_text('frozen evidence\n')
                with uproot.recreate(folder / 'result.root') as root:
                    root['histogram'] = (np.array([1., 2., 3.]), np.array([0., 1., 2., 3.]))
            compare_baseline_files(old, new)
            (new / 'currentRun.rndm').write_text('changed\n')
            with self.assertRaisesRegex(ValueError, 'currentRun'):
                compare_baseline_files(old, new)
            (new / 'currentRun.rndm').write_text('frozen evidence\n')
            with uproot.recreate(new / 'result.root') as root:
                root['histogram'] = (np.array([1., 2., 4.]), np.array([0., 1., 2., 3.]))
            with self.assertRaisesRegex(ValueError, 'histogram regression'):
                compare_baseline_files(old, new)


class RunFailureTests(unittest.TestCase):
    def exercise_first_task(self, log_text):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            executable = base / 'binary'
            executable.write_bytes(b'fake executable; subprocess is mocked')
            tasks = [{'id': name, 'events': 1, 'seeds': [11, 13]} for name in ('first', 'second')]
            manifest = {'phase': 'smoke', 'source_files_sha256': {}, 'tasks': tasks,
                        'configuration': {'readout_mode': 'boundary-entry'}}
            (base / 'manifest.json').write_text(json.dumps(manifest))
            gate = base / 'gate.json'
            gate.write_text(json.dumps({'status': 'passed', 'phase': 'geometry', 'source_files_sha256': {},
                                        'readout_mode': 'boundary-entry',
                                        'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest()}))
            for task in tasks:
                folder = base / task['id']; folder.mkdir()
                (folder / 'run.mac').write_text('/run/beamOn 1\n')
            def failed_ui(*args, **kwargs):
                kwargs['stdout'].write(log_text)
                return SimpleNamespace(returncode=0)
            with patch.object(runner, 'validate_study_inputs', return_value=manifest), \
                 patch.object(runner.subprocess, 'run', side_effect=failed_ui) as process, \
                 patch.dict(runner.os.environ, {'G4RUN_MANAGER_TYPE': 'Serial'}), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError) as failure:
                    runner.run(base, base, executable, gate)
                self.assertEqual(process.call_count, 1, 'runner launched the second task after failure')
                self.assertFalse((base / 'second/receipt.json').exists())
                self.assertFalse((base / 'second/simulation.log').exists())
            return json.loads((base / 'first/receipt.json').read_text()), str(failure.exception)

    def test_exit_zero_command_not_found_preserves_failure_and_stops(self):
        receipt, failure = self.exercise_first_task('***** COMMAND NOT FOUND </gps/clear> *****\n')
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('COMMAND NOT FOUND', receipt['ui_failure'][0])
        self.assertIn('COMMAND NOT FOUND', failure)

    def test_exit_zero_missing_outputs_stops(self):
        receipt, failure = self.exercise_first_task('Macro returned normally but produced no event files.\n')
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual(receipt['ui_failure'], [])
        self.assertEqual(receipt['missing_or_empty_outputs'], ['result.root', 'result_summary.csv'])
        self.assertIn('missing or empty event outputs', failure)

    def test_legacy_gate_cannot_qualify_corrected_readout(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            executable = base / 'binary'; executable.write_bytes(b'fake executable')
            manifest = {'phase': 'smoke', 'source_files_sha256': {}, 'tasks': [],
                        'configuration': {'readout_mode': 'boundary-entry'}}
            (base / 'manifest.json').write_text(json.dumps(manifest))
            gate = base / 'gate.json'
            gate.write_text(json.dumps({'status': 'passed', 'phase': 'geometry',
                                        'source_files_sha256': {}, 'readout_mode': 'legacy',
                                        'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest()}))
            with patch.object(runner, 'validate_study_inputs', return_value=manifest), \
                 patch.object(runner.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, 'different readout mode'):
                    runner.run(base, base, executable, gate)
                process.assert_not_called()
                self.assertFalse((base / 'execution.json').exists())

    def test_clear_failure_signatures_and_allowed_serial_warning(self):
        for text in ('***** Illegal parameter (0) </gps/foo> *****', 'FatalException',
                     'Batch is interrupted', 'command failed: /run/beamOn', 'Overlap is detected'):
            with self.subTest(text=text):
                self.assertEqual(runner.ui_failures(text), [text])
        self.assertEqual(runner.ui_failures('*** G4Exception : Analysis_W001\nMerging ntuples is not applicable in sequential application.'), [])


if __name__ == '__main__':
    unittest.main()
