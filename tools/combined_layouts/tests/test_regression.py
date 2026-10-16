"""Regression-tool tests use synthetic ROOT fixtures and mocked processes, never Geant4."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import uproot

SPEC = importlib.util.spec_from_file_location('combined_layout_regression', Path(__file__).resolve().parents[1] / 'regression.py')
regression = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regression)


def macro(mode='legacy', seeds=(123, 456)):
    return (f'# synthetic unit fixture\n/opnovice2/sipm/readoutMode {mode}\n'
            '/opnovice2/sipm/layout edge-two\n/opnovice2/sipm/face +X\n/run/initialize\n'
            '/random/setSeeds 17 27\n/geometry/test/run\n'
            f'/random/setSeeds {seeds[0]} {seeds[1]}\n/run/beamOn 10\n').encode()


def readout(rows):
    result = {'event_id': np.arange(rows, dtype=np.int32)}
    for name in ('sensor0_entries', 'sensor1_entries', 'sensor0_reflections', 'sensor1_reflections',
                 'sensor0_step_too_small', 'sensor1_step_too_small', 'no_rindex_steps'):
        result[name] = np.zeros(rows, dtype=np.int32)
    return result


def write_root(path, rows=0, extras=False, histogram_shift=False):
    scan = {'event_id': np.arange(10, dtype=np.int32), 'response': np.arange(10, dtype=np.int32),
            'missing': np.array([np.nan] + [1.0] * 9, dtype=np.float64)}
    data = readout(rows)
    if extras:
        data.update({name: np.zeros(rows, dtype=np.int32) for name in regression.EXTRA_COLUMNS['readout_events']})
    with uproot.recreate(path) as root:
        root.mktree('scan', {name: value.dtype for name, value in scan.items()})
        root['scan'].extend(scan)
        root.mktree('readout_events', {name: value.dtype for name, value in data.items()})
        if rows:
            root['readout_events'].extend(data)
        root['histogram'] = np.histogram([0.25, 0.75, 1.5 if not histogram_shift else 0.5], bins=[0, 1, 2])


def log(task):
    text = f'SiPM readout mode: {task["mode"]}\nGeometry overlaps check completed !\n'
    for i, y in enumerate((-25, 25)):
        text += f'SiPM placement: layout=edge-two, layer=0, local_sensor={i}, copy={i}, face=+X, local=({y},0,0) mm, world=(50.25,{y},0) mm\n'
    if task.get('layout_study'):
        text += 'Standalone layout geometry: PASS, synthetic unit fixture\n'
    return text


def reference_fixture(path, valid_root=False):
    path.mkdir()
    for phase, mode in regression.REFERENCES:
        campaign = path / phase
        campaign.mkdir()
        regression.write_json(campaign / 'execution.json', {'synthetic': True})
        tasks = []
        for index, case in enumerate(regression.CASES):
            folder = campaign / case / 'block-00'
            folder.mkdir(parents=True)
            seeds = [123 + index, 456 + index]
            (folder / 'run.mac').write_bytes(macro(mode, seeds))
            if valid_root:
                write_root(folder / 'result.root', rows=10 if mode == 'boundary-entry' else 0)
            else:
                (folder / 'result.root').write_bytes(b'unit fixture only')
            for name in ('result_summary.csv', 'currentEvent.rndm', 'currentRun.rndm'):
                (folder / name).write_bytes(('synthetic ' + name + '\n').encode())
            (folder / 'simulation.log').write_text(log({'mode': mode}))
            tasks.append({'id': case + '/block-00', 'case_id': case, 'block': 0, 'events': 10,
                          'seeds': seeds, 'macro_sha256': regression.digest(folder / 'run.mac')})
        source_hashes = {'old/source.cc': 'old source does not need to exist in the new checkout'}
        manifest = {'phase': 'smoke', 'configuration': {'readout_mode': mode}, 'tasks': tasks,
                    'source_files_sha256': source_hashes}
        regression.write_json(campaign / 'manifest.json', manifest)
        report_tasks = []
        for task in tasks:
            folder = campaign / task['id']
            receipt = {'status': 'passed', 'exit_code': 0, 'error': None, 'ui_failure': [],
                       'macro_sha256': task['macro_sha256'], 'manifest_sha256': regression.digest(campaign / 'manifest.json'),
                       'execution_sha256': regression.digest(campaign / 'execution.json'),
                       'log_sha256': regression.digest(folder / 'simulation.log'), 'executable_sha256': 'old-binary'}
            regression.write_json(folder / 'receipt.json', receipt)
            report_tasks.append({'task_id': task['id'], 'files_sha256': {name: regression.digest(folder / name)
                                  for name in ('run.mac', 'result.root', 'result_summary.csv', 'simulation.log', 'receipt.json')}})
        regression.write_json(path / (phase + '-audit.json'), {'status': 'passed', 'phase': 'smoke',
                              'readout_mode': mode, 'manifest_sha256': regression.digest(campaign / 'manifest.json'),
                              'source_files_sha256': source_hashes, 'executable_sha256': 'old-binary', 'tasks': report_tasks})


class MacroAndArraysTest(unittest.TestCase):
    def test_macro_variants_preserve_every_other_byte(self):
        old = macro()
        self.assertEqual(regression.make_macro(old, 'omitted', 'legacy', [123, 456]), old)
        added = regression.make_macro(old, 'enabled', 'legacy', [123, 456])
        command = b'/opnovice2/combined/layoutStudy true\n'
        self.assertEqual(added.count(command), 1)
        self.assertEqual(added.replace(command, b''), old)
        self.assertLess(added.index(command), added.index(b'/run/initialize'))

    def test_seed_or_nested_execution_change_is_rejected(self):
        with self.assertRaises(ValueError):
            regression.make_macro(macro(), 'enabled', 'legacy', [1, 2])
        with self.assertRaises(ValueError):
            regression.make_macro(b'/control/execute extra.mac\n' + macro(), 'enabled', 'legacy', [123, 456])
        with self.assertRaises(ValueError):
            regression.make_macro(macro().replace(b'/geometry/test/run\n', b''), 'enabled', 'legacy', [123, 456])

    def test_exact_arrays_keep_nan_but_reject_small_differences_and_dtype_drift(self):
        regression.exact_array(np.array([np.nan, 1.]), np.array([np.nan, 1.]), 'same')
        with self.assertRaises(ValueError):
            regression.exact_array(np.array([1.]), np.array([1. + 1e-12]), 'changed')
        with self.assertRaises(ValueError):
            regression.exact_array(np.array([1], dtype=np.int32), np.array([1], dtype=np.int64), 'dtype')

    def test_new_inactive_columns_must_be_zero_and_allowlisted(self):
        old = {'event_id': np.arange(10, dtype=np.int32), 'front_generated': np.arange(10, dtype=np.int32)}
        new = {**old, 'lineage_front_to_sensor2': np.zeros(10, dtype=np.int32)}
        regression.compare_table_arrays('combined_events', old, new, variant='omitted', mode='legacy')
        new['lineage_front_to_sensor2'][2] = 1
        with self.assertRaises(ValueError):
            regression.compare_table_arrays('combined_events', old, new, variant='omitted', mode='legacy')
        with self.assertRaises(ValueError):
            regression.compare_table_arrays('combined_events', old, {**old, 'unexpected': np.zeros(10)}, variant='omitted', mode='legacy')

    def test_passive_legacy_rows_are_only_allowed_for_enabled_variant(self):
        old, new = readout(0), readout(10)
        regression.compare_table_arrays('readout_events', old, new, variant='enabled', mode='legacy')
        for variant, mode in [('omitted', 'legacy'), ('enabled', 'boundary-entry')]:
            with self.assertRaises(ValueError):
                regression.compare_table_arrays('readout_events', old, new, variant=variant, mode=mode)
        new['no_rindex_steps'][0] = 1
        with self.assertRaises(ValueError):
            regression.compare_table_arrays('readout_events', old, new, variant='enabled', mode='legacy')

    def test_histogram_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new = Path(temp) / 'old.root', Path(temp) / 'new.root'
            write_root(old)
            write_root(new, extras=True)
            regression.compare_root_files(old, new, variant='omitted', mode='legacy')
            write_root(new, histogram_shift=True)
            with self.assertRaises(ValueError):
                regression.compare_root_files(old, new, variant='omitted', mode='legacy')


class CampaignTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = self.root / 'reference'
        self.campaign = self.root / 'campaign'

    def test_prepare_has_fixed_budget_relocates_and_refuses_overwrite(self):
        reference_fixture(self.reference)
        manifest = regression.prepare(self.reference, self.campaign)
        self.assertEqual(len(manifest['tasks']), 20)
        self.assertEqual(sum(t['events'] for t in manifest['tasks']), 200)
        regression.validate_inputs(self.campaign)
        relocated = self.root / 'relocated'
        shutil.copytree(self.reference, relocated)
        files, _, _ = regression.reference_inventory(relocated)
        self.assertEqual(files, manifest['reference_files_sha256'])
        with self.assertRaises(ValueError):
            regression.prepare(self.reference, self.campaign)
        (relocated / 'smoke/mono16/block-00/result.root').write_bytes(b'drift')
        with self.assertRaises(ValueError):
            regression.reference_inventory(relocated)

    def test_ui_failure_stops_dispatch_and_preserves_receipt(self):
        reference_fixture(self.reference)
        manifest = regression.prepare(self.reference, self.campaign)
        exe = self.root / 'mock-executable'
        exe.write_bytes(b'unit mock; never executed')
        exe.chmod(0o700)
        def rejected(args, **kwargs):
            kwargs['stdout'].write('COMMAND NOT FOUND\n')
            return subprocess.CompletedProcess(args, 0)
        with patch.object(regression.subprocess, 'run', side_effect=rejected) as mocked:
            with self.assertRaises(ValueError):
                regression.run(self.campaign, exe)
            self.assertEqual(mocked.call_count, 1)
        first, second = [self.campaign / task['id'] for task in manifest['tasks'][:2]]
        self.assertEqual(regression.read_json(first / 'receipt.json')['status'], 'failed')
        self.assertFalse((second / 'simulation.log').exists())
        with self.assertRaises(ValueError):
            regression.run(self.campaign, exe)

    def test_complete_synthetic_campaign_and_read_only_audit(self):
        reference_fixture(self.reference, valid_root=True)
        regression.prepare(self.reference, self.campaign)
        exe = self.root / 'mock-executable'
        exe.write_bytes(b'unit mock; never executed')
        exe.chmod(0o700)
        def synthetic(args, **kwargs):
            folder = kwargs['cwd']
            case, mode, variant = folder.parent.name, folder.parent.parent.name, folder.parent.parent.parent.name
            phase = next(phase for phase, m in regression.REFERENCES if m == mode)
            original = self.reference / phase / case / 'block-00'
            write_root(folder / 'result.root', rows=10 if mode == 'boundary-entry' or variant == 'enabled' else 0, extras=True)
            for name in ('result_summary.csv', 'currentEvent.rndm', 'currentRun.rndm'):
                shutil.copyfile(original / name, folder / name)
            kwargs['stdout'].write(log({'mode': mode, 'layout_study': variant == 'enabled'}))
            return subprocess.CompletedProcess(args, 0)
        with patch.object(regression.subprocess, 'run', side_effect=synthetic):
            regression.run(self.campaign, exe)
        before = {str(path.relative_to(self.root)): regression.digest(path) for path in self.root.rglob('*') if path.is_file()}
        report = regression.audit(self.campaign, self.reference, exe)
        after = {str(path.relative_to(self.root)): regression.digest(path) for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['engineering_events'], 200)
        self.assertEqual(len(report['tasks']), 20)
        output = self.root / 'audit.json'
        self.assertEqual(report, regression.audit(self.campaign, self.reference, exe, output))
        with self.assertRaises(ValueError):
            regression.audit(self.campaign, self.reference, exe, output)
        (self.campaign / report['tasks'][0]['task_id'] / 'currentEvent.rndm').write_bytes(b'changed RNG')
        with self.assertRaises(ValueError):
            regression.audit(self.campaign, self.reference, exe)


if __name__ == '__main__':
    unittest.main()
