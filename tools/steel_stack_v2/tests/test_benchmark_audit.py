"""Reject damaged resource evidence before recommending any production allocation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_audit as bench
from model import make_configuration, prepare_tasks, validate_tasks
from osc import sha256


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root/'bundle'
        self.bundle.mkdir()
        self.outputs = self.root/'results'
        self.config = make_configuration('back-center', 4, .5, 'painted-corner-v2', 16)
        self.tasks = prepare_tasks([self.config], events_per_task=2, blocks=1,
            campaign_seed=901, total_event_budget=2, stage='benchmark')
        self.task = self.tasks[0]
        self.task['purpose'] = bench.PURPOSE
        (self.bundle/'tasks.json').write_text(json.dumps(self.tasks))
        self.manifest = dict(purpose='benchmark', mock=False, total_event_budget=2,
            summary=validate_tasks(self.tasks), tasks_sha256=sha256(self.bundle/'tasks.json'),
            remote_output_root=str(self.outputs), executable_sha256='a'*64,
            source_manifest_sha256='b'*64, sif_sha256='c'*64, dataset_receipt_sha256='d'*64)
        (self.bundle/'manifest.json').write_text(json.dumps(self.manifest))
        self.manifest_hash = sha256(self.bundle/'manifest.json')
        self.attempt = self.outputs/self.task['task_id']/'job-42-task-1'
        self.run = self.attempt/'run'
        (self.run/'outputs').mkdir(parents=True)
        self.trace = self.run/'outputs/result.optical-numerics.jsonl'
        self.rows = [dict(kind='run', identity={**self.config['optical_numerics'],
            'surface_tolerance_mm':1.e-9, 'diagnostic_primary':False},
            boundary_class='PaintedCornerBoundary', post_step_order=[[p,True] for p in
            ('Transportation','OpAbsorption','OpBoundary','OpWLS2','Scintillation')])]
        self.rows += [dict(kind='event_end', event=i, corrections=0, boundary_no_rindex=0,
                          painted_zero_step_escapes=0, unsettled=0) for i in range(2)]
        self.rows.append(dict(kind='run_end'))
        self.save_trace()
        self.execution = dict(status='execution-complete-not-audited', exit_code=0,
            task_id=self.task['task_id'], array_index=1, events=2, purpose=bench.PURPOSE,
            optical_numerics=self.config['optical_numerics'], manifest_sha256=self.manifest_hash,
            run_dir=str(self.run), hostname='compute-fixture', scheduler_job='42',
            launcher_elapsed_seconds=20., child_max_rss_kib=102400,
            child_user_seconds=17., child_system_seconds=1.)
        for key in ('executable_sha256','source_manifest_sha256','sif_sha256','dataset_receipt_sha256'):
            self.execution[key] = self.manifest[key]
        self.store()
        patch = mock.patch.object(bench, 'audit_run', return_value={'passed':True})
        self.audit_run = patch.start()
        self.addCleanup(patch.stop)

    def save_trace(self):
        self.trace.write_text(''.join(json.dumps(row)+'\n' for row in self.rows))

    def store(self):
        self.execution['artifacts'] = {p.relative_to(self.run).as_posix():sha256(p)
                                       for p in self.run.rglob('*') if p.is_file()}
        (self.attempt/'execution.json').write_text(json.dumps(self.execution))

    def collect(self):
        return bench.collect(self.bundle, self.outputs, self.manifest_hash)

    def test_resource_report_calls_strict_ledger_audit_with_seeds(self):
        report = self.collect()
        self.assertEqual(report['events'], 2)
        self.assertEqual(report['resource_summary']['peak_child_rss_mib'], 100)
        self.assertEqual(report['resource_summary']['one_hour_event_ceiling_with_2x_observed_time_margin'], 165)
        self.assertIsNone(report['gap_selected'])
        self.audit_run.assert_called_once_with(self.run.resolve(), self.config, 2,
            expected_seeds=(self.task['seed1'],self.task['seed2']))

    def test_missing_or_ambiguous_attempt_is_rejected(self):
        p = self.attempt/'execution.json'
        original = p.read_text()
        p.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or ambiguous'): self.collect()
        p.write_text(original)
        other = self.attempt.parent/'job-43-task-1'
        other.mkdir()
        (other/'execution.json').write_text(original)
        with self.assertRaisesRegex(ValueError, 'Missing or ambiguous'): self.collect()

    def test_manifest_or_artifact_tampering_is_rejected(self):
        (self.bundle/'manifest.json').write_text(json.dumps(self.manifest)+' ')
        with self.assertRaisesRegex(ValueError, 'Submitted manifest'): self.collect()
        (self.bundle/'manifest.json').write_text(json.dumps(self.manifest))
        self.trace.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'inventory/checksum'): self.collect()

    def test_extra_or_removed_output_is_rejected(self):
        extra = self.run/'unexpected.txt'
        extra.write_text('unregistered')
        with self.assertRaisesRegex(ValueError, 'inventory/checksum'): self.collect()
        extra.unlink()
        self.trace.unlink()
        with self.assertRaisesRegex(ValueError, 'inventory/checksum'): self.collect()

    def test_mismatched_task_environment_or_failed_execution_is_rejected(self):
        original = copy.deepcopy(self.execution)
        for field, value in [('task_id','another'), ('events',3), ('array_index',2),
                             ('purpose','science'), ('source_manifest_sha256','f'*64),
                             ('status','failed'), ('exit_code',1)]:
            self.execution = {**original, field:value}
            self.store()
            with self.subTest(field=field), self.assertRaises(ValueError): self.collect()

    def test_numerical_failure_and_missing_end_are_not_hidden_by_exit_zero(self):
        for field in ('boundary_no_rindex','painted_zero_step_escapes','unsettled'):
            self.rows[1][field] = 1
            self.save_trace(); self.store()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'transport hard failure'):
                self.collect()
            self.rows[1][field] = 0
        self.rows.pop(1)
        self.save_trace(); self.store()
        with self.assertRaisesRegex(ValueError, 'Missing numerical event'): self.collect()

    def test_bad_memory_and_nonfinite_resource_values_are_rejected(self):
        for field, value in [('child_max_rss_kib',0), ('launcher_elapsed_seconds',float('nan')),
                             ('child_user_seconds',float('inf'))]:
            original = self.execution[field]
            self.execution[field] = value
            self.store()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'resource measurements'):
                self.collect()
            self.execution[field] = original

    def correction(self):
        return dict(kind='correction',event=0,track=1,reflection_step=5,step=5,
            raw_status=7,faces=3,tile=1,tolerance_mm=1.e-9,scale=16,
            before_mm=[0.,0.,0.],after_mm=[16.e-9,16.e-9,0.],displacement_mm=2**.5*16.e-9,
            pre=['Tank',1],raw_post=['World',0],post=['Tank',1],next=['Tank',1],
            same_volume_navigator_verified=True,transport_cache_verified=True,
            physical_proposals_delegated_unchanged=True,particle_change_modified_fields=['position','geometry_state'])

    def test_valid_correction_and_physical_mutation_rejection(self):
        c = self.correction()
        self.rows[1]['corrections'] = 1
        self.rows[1:1] = [c, dict(kind='boundary',event=0,track=1,step=5,status=7,
            edge_reflection=True,painted_zero_step_escape=False),
            dict(kind='terminal',event=0,track=1,status=9,sensor=-1)]
        self.save_trace(); self.store()
        self.assertEqual(self.collect()['rows'][0]['numerical']['corrections'],1)
        for field, value in [('raw_status',13),('scale',32),('transport_cache_verified',False),
                             ('post',['World',0]),('particle_change_modified_fields',['energy']),
                             ('after_mm',[1.,1.,0.])]:
            original = c[field]
            c[field] = value
            self.save_trace(); self.store()
            with self.subTest(field=field), self.assertRaises(ValueError): self.collect()
            c[field] = original

    def test_repeated_correction_and_ledger_failure_are_rejected(self):
        self.rows[1:1] = [self.correction(),self.correction()]
        self.save_trace(); self.store()
        with self.assertRaisesRegex(ValueError, 'Repeated numerical correction'): self.collect()
        self.audit_run.side_effect = ValueError('Ledger failure')
        with self.assertRaisesRegex(ValueError, 'Ledger failure'): self.collect()


if __name__ == '__main__':
    unittest.main()
