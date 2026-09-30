import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import remote_qualification as qualification
import remote_build
import corner_audit

class RemoteQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'ray-inputs.json').write_text(json.dumps([
            {'probe_id':'captured','family':'captured'},{'probe_id':'steel','family':'control-steel'}]))
        self.jobs=[];self.results={}
        for scale in (0,16,32,64):
            self.jobs.extend([dict(job_id=f'p{scale}',kind='photons',scale=scale),dict(job_id=f'n{scale}',kind='neutrons',scale=scale)])
            def event(name,failed=False):
                return dict(id=name,end={'corrections':0,'boundary_no_rindex':int(failed),'painted_zero_step_escapes':int(failed),'rng_end':'fixed'},
                    physical='fixed',sequence=[],terminal=[(1,0,-1,'OpAbsorption',-1,['Tank',1])])
            self.results[f'p{scale}']=dict(header={'post_step_order':['Transportation','OpBoundary']},
                events={0:event('captured',scale==0),1:event('steel',True)})
            self.results[f'n{scale}']=dict(header={'post_step_order':['Transportation','OpBoundary']},events={0:event('neutron')})
        self.registry={'jobs':self.jobs}

    def assess(self):
        with patch.object(corner_audit,'inspect_job',side_effect=lambda job:self.results[job['job_id']]):
            return qualification.assess(self.root,self.registry)

    def test_within_architecture_controls_and_stability_pass(self):
        self.assertTrue(self.assess()['passed'])
        self.assertFalse(self.assess()['local_arm64_event_equality_required'])

    def test_air_absorption_does_not_hide_a_leak(self):
        self.results['p16']['events'][0]['terminal'][0]=(1,0,-1,'OpAbsorption',-1,['World',0])
        self.assertFalse(self.assess()['passed'])

    def test_changed_negative_control_rng_fails(self):
        self.results['p32']['events'][1]['end']['rng_end']='changed'
        self.assertFalse(self.assess()['passed'])

    def test_scale_disagreement_fails(self):
        self.results['p32']['events'][0]['sequence']=['different']
        self.assertFalse(self.assess()['passed'])

    def test_vacuous_legacy_reproduction_fails(self):
        self.results['p0']['events'][0]['end'].update(boundary_no_rindex=0,painted_zero_step_escapes=0)
        self.assertFalse(self.assess()['passed'])

    def test_event_execution_requires_compute_allocation(self):
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(ValueError,'compute allocation'):
            qualification.execute(self.root)

    def test_remote_build_rejects_local_architecture_before_writing(self):
        with patch.object(remote_build.platform,'system',return_value='Darwin'),self.assertRaisesRegex(ValueError,'Linux/x86_64'):
            remote_build.build(self.root,'unknown',self.root/'image',self.root/'data',2)

    def test_compile_parallelism_is_bounded(self):
        with patch.object(remote_build.platform,'system',return_value='Linux'),patch.object(remote_build.platform,'machine',return_value='x86_64'),self.assertRaisesRegex(ValueError,'bounded'):
            remote_build.build(self.root,'unknown',self.root/'image',self.root/'data',100)

if __name__=='__main__':unittest.main()
