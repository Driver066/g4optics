import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from corner_cases import ray_cases,inputs,table
from model import make_configuration,configuration_hash,validate_configuration
from acceptance import scan_args
from osc import scan_args as osc_args
from audit import check_macro,check_run_config
import tempfile

class CornerInterfaceTests(unittest.TestCase):
    def test_preregistered_ray_coverage_and_inputs(self):
        cases=ray_cases()
        self.assertEqual(len(cases),165)
        self.assertEqual(sum(c['family']=='edge' for c in cases),108)
        self.assertEqual(sum(c['family']=='vertex' for c in cases),40)
        self.assertEqual(sum(c['family'].startswith('control-') for c in cases),16)
        self.assertEqual(len(inputs())*4,10560)
        self.assertEqual(table(inputs()),table(inputs()))
        for c in cases:
            d=c['direction'];p,q=c['polarizations']
            for v in (p,q):
                self.assertAlmostEqual(sum(x*x for x in v),1,places=13)
                self.assertAlmostEqual(sum(x*y for x,y in zip(d,v)),0,places=13)
            self.assertAlmostEqual(sum(x*y for x,y in zip(p,q)),0,places=13)

    def test_numerical_identity_cannot_collapse(self):
        configs=[make_configuration('back-four',24,.5,'legacy' if s==0 else 'painted-corner-v1',s) for s in (0,16,32,64)]
        self.assertEqual(len({configuration_hash(c) for c in configs}),4)
        old=copy.deepcopy(configs[0]);old.pop('optical_numerics')
        validate_configuration(old)
        self.assertNotEqual(configuration_hash(old),configuration_hash(configs[0]))
        for mode,scale in (('legacy',16),('painted-corner-v1',0),('painted-corner-v1',128),('unknown',0)):
            with self.assertRaises(ValueError):make_configuration('back-four',24,.5,mode,scale)

    def test_local_and_osc_arguments_keep_identity(self):
        for scale in (0,16,32,64):
            cfg=make_configuration('back-four',24,.5,'legacy' if scale==0 else 'painted-corner-v1',scale)
            task=dict(config=cfg,preset='steel-module-stack-v2',layout='back-four',tile_thickness_mm=24,
                      readout_gap_mm=.5,events=2,seed1=1,seed2=2,accounting=True)
            for args in (scan_args(task),osc_args(task)):
                self.assertEqual(args[args.index('--optical-numerics')+1],cfg['optical_numerics']['profile'])
                if scale:self.assertEqual(args[args.index('--optical-corner-scale')+1],str(scale))

    def test_audit_rejects_numerical_config_mismatch(self):
        cfg=make_configuration('back-four',24,.5,'painted-corner-v1',16)
        with self.assertRaisesRegex(ValueError,'numerical identity'):
            check_run_config({'study_preset':'steel-module-stack-v2','stack':{'optical_numerics':{'profile':'legacy','scale':0}}},cfg,True,(1,2))

    def test_neutron_macro_audit_rejects_diagnostic_source(self):
        cfg=make_configuration('back-four',24,.5)
        commands='''/opnovice2/stack/model v2
/opnovice2/stack/enabled true
/opnovice2/stack/layers 10
/opnovice2/sipm/layout back-four
/opnovice2/diagnostics/stackPhotonAccounting true
/gps/particle neutron
/gps/pos/type Point
/opnovice2/numerics/mode legacy
/opnovice2/numerics/cornerScale 0
/opnovice2/numerics/probeFile not-production.txt
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.mac';path.write_text(commands)
            with self.assertRaisesRegex(ValueError,'Diagnostic rays'):
                check_macro(path,cfg,True,None)

if __name__=='__main__':unittest.main()
