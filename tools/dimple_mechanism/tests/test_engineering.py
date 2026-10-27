"""Counterexample tests for model isolation, frozen inputs, and physical audits."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import audit
import engineering
import model

REPO = TOOLS.parents[1]
TEMPLATE = (REPO/model.TEMPLATE).read_text()


def minimal_scan():
    floating = dict(shoot_x_mm=[0.,0.], shoot_y_mm=[0.,0.], shoot_z_mm=[18.,18.],
        primary_kinetic_energy_mev=[1.,1.], collection_efficiency=[.2,np.nan],
        steel_edep_mev=[0.,0.], tile_edep_mev=[.3,0.], electron_tile_edep_mev=[.2,0.],
        scint_centroid_x_mm=[0.,np.nan], scint_centroid_y_mm=[0.,np.nan], scint_centroid_z_mm=[11.,np.nan])
    integer = dict(event_id=[0,1], generated_optical_photons=[10,0], scintillation_photons=[10,0],
        cerenkov_photons=[0,0], sipm_detected_photons=[2,0], collection_efficiency_valid=[1,0],
        scint_centroid_valid=[1,0], sipm_sensor_0_detected_photons=[2,0], sipm_sensor_1_detected_photons=[0,0],
        sipm_sensor_2_detected_photons=[0,0], sipm_sensor_3_detected_photons=[0,0])
    return {**{k:np.array(v,dtype=float) for k,v in floating.items()}, **{k:np.array(v,dtype=np.int32) for k,v in integer.items()}}


def flat_geometry_log():
    return """SiPM geometry: full_size=(2,2,0.5) mm
SiPM placement: layout=single, layer=0, local_sensor=0, copy=0, face=-Z, local=(0,0,0) mm, world=(0,0,-12.25) mm
Checking overlaps for volume Tank:0 (G4Box) ... OK!
Checking overlaps for volume SiPM:0 (G4Box) ... OK!
Geometry overlaps check completed !
Issue9 geometry: shape=flat front_compensation=0 reference_radius_mm=3 cube_side_mm=3.8383165853550265 removed_volume_mm3=0 restored_volume_mm3=0 nominal_volume_mm3=240000 analytic_scintillator_volume_mm3=240000 front_extent_mm=0 receiving_plane_z_mm=-12 solid_type=G4Box
Issue9 ray: label=center x_mm=0 y_mm=0 entry_z_mm=12 exit_z_mm=-12 thickness_mm=24
Issue9 ray: label=farx x_mm=40 y_mm=0 entry_z_mm=12 exit_z_mm=-12 thickness_mm=24
Issue9 ray: label=fary x_mm=0 y_mm=40 entry_z_mm=12 exit_z_mm=-12 thickness_mm=24
Issue9 inside: label=above x_mm=0 y_mm=0 z_mm=12.1 observed=0
Issue9 inside: label=center x_mm=0 y_mm=0 z_mm=0 observed=2
Issue9 inside: label=below x_mm=0 y_mm=0 z_mm=-12.1 observed=0
Issue9 geometry audit: PASS rays=3 inside_probes=3
"""


class ModelTests(unittest.TestCase):
    def test_fixed_model_aperture_controls_and_no_production_path(self):
        configs = model.CONFIGURATIONS
        self.assertEqual([(x['enabled'],x['sensor_mm']) for x in configs[:4]],
                         [(False,2.),(True,2.),(False,6.),(True,6.)])
        cases = model.cases()
        self.assertEqual(sum(x['events'] for x in cases),42)
        for case in cases:
            text = model.macro(TEMPLATE,case)
            if case['stage']=='geometry': self.assertNotIn('/run/beamOn',text)
            else:
                self.assertIn('/gps/pos/centre '+f"{case['x_mm']:g} 0 18 mm",text)
                self.assertIn('/run/beamOn 2',text)
                self.assertNotIn('/opnovice2/gun/randomDirection',text)
        bad = copy.deepcopy(cases[-1]); bad['events']=1000
        with self.assertRaisesRegex(ValueError,'fixed at two'): model.macro(TEMPLATE,bad)
        bad = copy.deepcopy(cases[-1]); bad['config']['sensor_mm']=3.
        with self.assertRaisesRegex(ValueError,'approved seven'): model.macro(TEMPLATE,bad)

    def test_material_physics_commands_are_preserved(self):
        text=model.macro(TEMPLATE,model.cases()[-1])
        for command in model.material_commands(TEMPLATE).splitlines(): self.assertIn(command,text)
        self.assertNotIn('/process/inactivate Scintillation',text)

    def test_analytic_ray_and_inside_distinguish_compensation(self):
        hemi=model.CONFIGURATIONS[1]; compensated=model.CONFIGURATIONS[4]; cube=model.CONFIGURATIONS[6]
        self.assertEqual(audit.cavity_height(hemi,0,0),3.)
        self.assertEqual(audit.expected_inside(hemi,0,0,-11.),0)
        self.assertEqual(audit.expected_inside(hemi,0,0,14.),0)
        self.assertEqual(audit.expected_inside(compensated,0,0,14.),2)
        self.assertEqual(audit.expected_inside(cube,2.,0,-11.),2)
        self.assertEqual(audit.expected_inside(hemi,2.,0,-11.),0)


class OutputAuditTests(unittest.TestCase):
    def test_real_source_and_zero_events_remain_untouched(self):
        scan=minimal_scan(); before=scan['shoot_z_mm'].copy()
        report=audit.audit_scan(scan,dict(events=2,x_mm=0.))
        self.assertEqual(report['events'],2)
        self.assertEqual(report['electron_positron_tile_edep_sum_mev'],.2)
        np.testing.assert_array_equal(scan['shoot_z_mm'],before)
        scan['shoot_z_mm'][:]=4.
        with self.assertRaisesRegex(ValueError,'source shoot_z'): audit.audit_scan(scan,dict(events=2,x_mm=0.))

    def test_invalid_energy_centroid_and_accounting_fail(self):
        for key,value,reason in [('electron_tile_edep_mev',float('nan'),'energy'),
                                  ('scint_centroid_z_mm',float('nan'),'centroid'),
                                  ('sipm_sensor_1_detected_photons',1,'sum of four')]:
            scan=minimal_scan(); scan[key][0]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,reason):
                audit.audit_scan(scan,dict(events=2,x_mm=0.))

    def test_geometry_must_match_actual_navigation_not_self_report(self):
        log=flat_geometry_log()
        self.assertEqual(audit.audit_geometry(log,model.CONFIGURATIONS[0])['rays'],3)
        for bad,reason in [(log.replace('exit_z_mm=-12','exit_z_mm=-9'),'ray failed'),
                           (log.replace('z_mm=0 observed=2','z_mm=0 observed=0'),'Inside failed'),
                           (log.replace('world=(0,0,-12.25)','world=(0,0,-2.75)'),'dimensions/position'),
                           (log.replace('PASS rays=3','PASS rays=9'),'completion')]:
            with self.subTest(reason=reason),self.assertRaisesRegex(ValueError,reason):
                audit.audit_geometry(bad,model.CONFIGURATIONS[0])


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'repo'; self.root.mkdir()
        app=self.root/'test/OpNovice2'; app.mkdir(parents=True)
        for name,text in [('CMakeLists.txt','project(example)'),('OpNovice2.cc','int main() {}'),
                           ('ej200_sipm_gps_test.mac',TEMPLATE)]: (app/name).write_text(text)
        for relative in ['tools/dimple_mechanism/engineering.py','tools/dimple_study/optical_audit.py','tools/layout_study/audit.py']:
            target=self.root/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_text('# frozen fixture')
        self.inventory=Path(self.tmp.name)/'datasets'; self.inventory.write_text('reference dataset hash receipt\n')
        self.batch=Path(self.tmp.name)/'batch'
        self.patcher=patch.object(engineering,'ROOT',self.root); self.patcher.start(); self.addCleanup(self.patcher.stop)
        def git(*args):
            return TEMPLATE.encode() if args[0]=='show' else b'fixture\n'
        self.gitpatch=patch.object(engineering,'git',side_effect=git); self.gitpatch.start(); self.addCleanup(self.gitpatch.stop)

    def prepare(self): engineering.prepare(self.batch,'sha256:'+'a'*64,self.inventory)

    def test_freeze_rejects_overwrite_source_tool_and_extra_file(self):
        self.prepare(); engineering.verify_prepared(self.batch)
        with self.assertRaises(FileExistsError): self.prepare()
        path=self.batch/'source/test/OpNovice2/OpNovice2.cc'; old=path.read_bytes(); path.write_text('changed')
        with self.assertRaisesRegex(ValueError,'source/tool changed'): engineering.verify_prepared(self.batch)
        path.write_bytes(old)
        extra=path.with_name('unrecorded.cc'); extra.write_text('new CMake glob input')
        with self.assertRaisesRegex(ValueError,'inventory changed'): engineering.verify_prepared(self.batch)

    def test_frozen_macro_and_manifest_mutation_are_rejected(self):
        self.prepare(); macro=self.batch/'cases/geometry-A/run.mac'; original=macro.read_bytes(); macro.write_text('wrong macro')
        with self.assertRaisesRegex(ValueError,'macro changed'): engineering.verify_prepared(self.batch)
        macro.write_bytes(original)
        manifest=self.batch/'manifest.json'; manifest.write_text(manifest.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'manifest changed'): engineering.verify_prepared(self.batch)

    def test_changed_optical_template_is_rejected_before_batch_creation(self):
        (self.root/model.TEMPLATE).write_text(TEMPLATE.replace('1.58','1.1'))
        with self.assertRaisesRegex(ValueError,'historical source'): self.prepare()
        self.assertFalse(self.batch.exists())

    def test_build_receipt_rejects_binary_replacement(self):
        self.prepare(); manifest=engineering.verify_prepared(self.batch)
        (self.batch/'build').mkdir(); binary=self.batch/'build/OpNovice2'; binary.write_bytes(b'original')
        (self.batch/'build.log').write_text('accepted build')
        engineering.save(self.batch/'build-receipt.json',dict(manifest_sha256=engineering.sha(self.batch/'manifest.json'),
            source_sha256=manifest['source_sha256'],exit_code=0,build_log_sha256=engineering.sha(self.batch/'build.log'),
            executable_sha256=engineering.sha(binary)))
        engineering.verify_build(self.batch,manifest)
        binary.write_bytes(b'unrelated binary')
        with self.assertRaisesRegex(ValueError,'Build does not match'):
            engineering.verify_build(self.batch,manifest)


if __name__=='__main__': unittest.main()
