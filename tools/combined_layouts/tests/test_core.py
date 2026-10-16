"""Failure-injection checks for the 40-cell matrix, physical geometry and readout semantics."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

DIRECTORY=Path(__file__).resolve().parents[1];sys.path.insert(0,str(DIRECTORY))
import study
import validation
# Unique module names keep these tests independent of the frozen predecessor tool modules.
def load(name,filename):
 spec=importlib.util.spec_from_file_location(name,DIRECTORY/filename);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
preparation=load('combined_layout_preparation','prepare.py')
analysis=load('combined_layout_analysis','analyze.py')
runner=load('combined_layout_runner','run.py')
REPO=DIRECTORY.parents[1]


def config(mode='boundary-entry',layout='back-four',geometry='joined8-8'):
 return next(c for c in study.configurations() if (c['readout_mode'],c['layout'],c['geometry'])==(mode,layout,geometry))


def fixture(c):
 n=c['sensor_count'];legacy=c['readout_mode']=='legacy';s={}
 for key,val in {'event_id':[0,1],'primary_kinetic_energy_mev':[.3,.5],'shoot_x_mm':[0.,0.],'shoot_y_mm':[0.,0.],
  'shoot_z_mm':[c['source_z_mm']]*2,'hit_valid':[1,0],'hit_z_mm':[c['envelope_thickness_mm']/2,np.nan],
  'generated_optical_photons':[6,0],'scintillation_photons':[6,0],'cerenkov_photons':[0,0],
  'sipm_detected_photons':[(3 if legacy else 2)*n,0],
  'collection_efficiency_valid':[1,0],'collection_efficiency':[(3 if legacy else 2)*n/6,np.nan],
  'tile_edep_mev':[.3,0.],'electron_tile_edep_mev':[.3,0.],'proton_tile_edep_mev':[0.,0.],
  'other_charged_tile_edep_mev':[0.,0.],'neutral_tile_edep_mev':[0.,0.],'steel_edep_mev':[0.,0.]}.items():s[key]=np.array(val)
 r={'event_id':np.arange(2),'no_rindex_steps':np.zeros(2,dtype=int)}
 ce={'event_id':np.arange(2)}
 for i in range(4):
  s[f'sipm_sensor_{i}_detected_photons']=np.array([(3 if legacy else 2) if i<n else 0,0])
  for suffix,val in [('entries',2),('reflections',1 if legacy else 0),('step_too_small',0)]:r[f'sensor{i}_{suffix}']=np.array([val if i<n else 0,0])
  for p in ('front','back'):ce[f'lineage_{p}_to_sensor{i}']=s[f'sipm_sensor_{i}_detected_photons'].copy() if p=='front' else np.zeros(2,dtype=int)
  ce[f'unknown_to_sensor{i}']=np.zeros(2,dtype=int)
 for key,value in {'front_generated':5,'back_generated':1,'all_births_front':6,'all_births_back':2,'all_births_joint':0,'all_births_other':0,
    'optical_parent_births':2,'joint_boundary_steps':1,'no_rindex_steps':0,'front_edep_mev':.1,'back_edep_mev':.2,'joint_edep_mev':.01,
    'front_optical_edep_mev':.01,'back_optical_edep_mev':.02,'joint_optical_edep_mev':.001}.items():ce[key]=np.array([value,0])
 rows=[(0,0,2,2,1)]
 for i in range(n):
  rows.append((0,0,study.SENSOR_REGIONS[i],2,2))
  if legacy:rows.extend([(0,0,study.SENSOR_REGIONS[i],3,1),(0,study.SENSOR_REGIONS[i],0,13,1)])
 b={key:np.array([row[i] for row in rows]) for i,key in enumerate(('event_id','pre_region','post_region','status','count'))}
 ce['boundary_steps']=np.array([sum(row[-1] for row in rows),0])
 if not c['combined']:ce,b={'event_id':np.array([],dtype=int)},{'event_id':np.array([],dtype=int)}
 return {'scan':s,'combined_events':ce,'combined_boundaries':b,'readout_events':r}


def geometry_log(c):
 vec=lambda values:'('+','.join(f'{v:g}' for v in values)+')';half=c['envelope_thickness_mm']/2;n=c['sensor_count']
 lines=[f'Standalone layout study: layout={c["detector_layout"]}, combined={str(c["combined"]).lower()}, active_sensors={n}, active_thickness={c["active_thickness_mm"]:g} mm, envelope_thickness={2*half:g} mm, assembly_z_min={-half:g} mm, assembly_z_max={half:g} mm, readout={c["readout_mode"]}']
 boxes=[]
 if c['combined']:
  for name,copy,center,t in [('TankFront',0,c['front_center_z_mm'],c['front_mm']),('TankBack',1,c['back_center_z_mm'],c['back_mm']),('CombinedGrease',0,c['joint_center_z_mm'],c['gap_mm'])]:boxes.append((name,copy,[-50,-50,center-t/2],[50,50,center+t/2]))
 else:boxes.append(('Tank',0,[-50,-50,-half],[50,50,half]))
 for i,(local,center) in enumerate(zip(c['local_centers'],c['sensor_centers_mm'])):
  lines.append(f'SiPM placement: layout={c["detector_layout"]}, layer=0, local_sensor={i}, copy={i}, face={c["face"]}, local={vec([*local,0])} mm, world={vec(center)} mm')
  size=np.array([.5,2.4,2.4] if c['face']=='+X' else [2.4,2.4,.5]);boxes.append(('SiPM',i,np.array(center)-size/2,np.array(center)+size/2))
  areas=[0.,0.,0.,0.]
  if not c['combined']:areas[0]=5.76
  elif c['face']=='-Z':areas[3]=5.76
  else:
   j=c['joint_center_z_mm'];lo,hi=j-.05,j+.05
   area=lambda a,b:2.4*max(0,min(1.2,b)-max(-1.2,a))
   areas[1:]=[area(hi,half),area(lo,hi),area(-half,lo)]
  lines.append(f'Standalone sensor contact: copy={i}, face={c["face"]}, center={vec(center)} mm, full_size={vec(size)} mm, '+', '.join(f'{key}_area={value:g} mm2' for key,value in zip(('mono','front','joint','back'),areas))+', total_area=5.76 mm2, coupling_gap=0 mm')
 for name,copy,low,high in boxes:
  lines.append(f'Checking overlaps for volume {name}:{copy} (G4Box) ... OK!')
  lines.append(f'Standalone volume bounds: name={name}, copy={copy}, min={vec(low)} mm, max={vec(high)} mm')
 lines.extend([f'Standalone layout geometry: PASS, volumes={len(boxes)}, tiles={2 if c["combined"] else 1}, joints={1 if c["combined"] else 0}, sensors={n}, no_positive_volume_overlap=true, outer_contact=true','Geometry overlaps check completed !'])
 return '\n'.join(lines)


class MatrixTests(unittest.TestCase):
 def test_all_configs_coordinates_and_unique_phase_seeds(self):
  cs=study.configurations();self.assertEqual(len(cs),40)
  allseeds=[]
  for phase,(_,blocks) in study.PHASES.items():
   for i,c in enumerate(cs):
    if c['face']=='-Z':self.assertEqual(c['sensor_centers_mm'][0][2],-c['envelope_thickness_mm']/2-.25)
    for block in range(blocks):
     for geo in (False,True):allseeds.extend(study.seed_pair(i,phase,block,geo))
  self.assertEqual(len(allseeds),len(set(allseeds)))
  self.assertTrue(all(0<x<2147483647 for x in allseeds))
 def test_frozen_prepare_inputs_budget_and_nonoverwrite(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'inputs';m=preparation.prepare(REPO,path,'explore')
   self.assertEqual(len(m['tasks']),80);self.assertEqual(m['execution']['total_events'],8000)
   validation.validate_inputs(path,REPO)
   with self.assertRaisesRegex(ValueError,'existing'):preparation.prepare(REPO,path,'explore')
   f=path/m['tasks'][0]['id']/'run.mac';f.write_text(f.read_text().replace('/run/beamOn 100','/run/beamOn 101'))
   with self.assertRaisesRegex(ValueError,'macro hash'):validation.validate_inputs(path,REPO)
 def test_missing_frozen_dependency_is_rejected(self):
  with tempfile.TemporaryDirectory() as temp:
   target=Path(temp)/'inputs';m=preparation.prepare(REPO,target,'geometry')
   del m['source_files_sha256']['test/OpNovice2/src/SteppingAction.cc']
   (target/'manifest.json').write_text(json.dumps(m))
   with self.assertRaisesRegex(ValueError,'inventory incomplete'):validation.validate_inputs(target,REPO)
 def test_old_seed_collision_prevents_output(self):
  first=study.seed_pair(0,'smoke',0)[0]
  with tempfile.TemporaryDirectory() as temp,patch.object(preparation,'old_seed_registry',return_value={'reserved_seed_values':[first],'files_sha256':{}}):
   target=Path(temp)/'blocked'
   with self.assertRaisesRegex(ValueError,'overlaps old'):preparation.prepare(REPO,target,'smoke')
   self.assertFalse(target.exists())


class AuditTests(unittest.TestCase):
 def test_all_40_geometry_and_event_contracts(self):
  for c in study.configurations():
   with self.subTest(config=c['id']):
    validation.audit_log(geometry_log(c),c)
    r=validation.audit_tables(fixture(c),c,2)
    self.assertEqual(r['events'],2);self.assertEqual(r['zero_D_events'],1)
    self.assertEqual(r['readout']['legacy_excess_over_true_entries'],c['sensor_count'] if c['readout_mode']=='legacy' else 0)
 def test_005_mm_back_overlap_not_hidden_by_pass_log(self):
  c=config(layout='back-center');text=geometry_log(c).replace('world=(0,0,-8.3)','world=(0,0,-8.25)')
  with self.assertRaisesRegex(ValueError,'sensor center'):validation.audit_log(text,c)
 def test_sensor2_boundary_id_is_not_world(self):
  c=config();t=fixture(c);t['combined_boundaries']['post_region'][3]=5
  with self.assertRaisesRegex(ValueError,'sensor 2'):validation.audit_tables(t,c,2)
 def test_corrected_reflection_false_tally_rejected(self):
  c=config();t=fixture(c);t['readout_events']['sensor2_entries'][0]=0
  with self.assertRaisesRegex(ValueError,'corrected sensor 2'):validation.audit_tables(t,c,2)
 def test_legacy_negative_excess_rejected(self):
  c=config('legacy');t=fixture(c);t['readout_events']['sensor2_entries'][0]=4
  with self.assertRaisesRegex(ValueError,'below observed'):validation.audit_tables(t,c,2)
 def test_missing_zero_event_and_zero_denominator_rejected(self):
  c=config();t=fixture(c);t['scan']['event_id'][1]=0
  with self.assertRaisesRegex(ValueError,'event coverage'):validation.audit_tables(t,c,2)
  t=fixture(c);t['scan']['collection_efficiency'][1]=0
  with self.assertRaisesRegex(ValueError,'NaN'):validation.audit_tables(t,c,2)
 def test_inactive_copy_rejected(self):
  c=config(layout='back-center');t=fixture(c);t['scan']['sipm_sensor_3_detected_photons'][0]=1
  with self.assertRaisesRegex(ValueError,'inactive'):validation.audit_tables(t,c,2)


class BootstrapTests(unittest.TestCase):
 def test_joint_matrix_contrasts_share_mono24_exactly(self):
  ids=[c['id'] for c in study.configurations()];blocks={key:[np.array([0.,10.]),np.array([0.,20.])] for key in ids}
  matrix=analysis.joint_bootstrap(blocks,ids,10000,7)
  self.assertTrue(np.array_equal(matrix,analysis.joint_bootstrap(blocks,ids,10000,7)))
  points=np.array([7.5]*40);comparisons,interactions=analysis.summarize_contrasts(points,matrix,ids)
  self.assertEqual(len(comparisons),52);self.assertEqual(len(interactions),18)
  i=ids.index('legacy/side-two/mono24');j=ids.index('legacy/side-two/joined8-16')
  row=next(r for r in comparisons if r['target']==ids[j] and r['reference']==ids[i])
  self.assertEqual(row['difference_ci95'],np.quantile(matrix[:,j]-matrix[:,i],[.025,.975]).tolist())
  self.assertEqual(row['difference_D_per_source'],0)
 def test_known_constant_joining_and_interaction(self):
  cs=study.configurations();ids=[c['id'] for c in cs]
  points=np.array([20. if c['combined'] else 40. for c in cs]);matrix=np.tile(points,(10000,1))
  direct,interaction=analysis.summarize_contrasts(points,matrix,ids)
  self.assertTrue(all(r['ratio']==.5 for r in direct if r['kind']=='joining'))
  self.assertTrue(all(r['Q_ratio_of_joining_ratios']==1 and r['Q_ci95']==[1,1] for r in interaction))


class RunTests(unittest.TestCase):
 def test_exit_zero_ui_error_stops_serial_dispatch_and_preserves_receipt(self):
  with tempfile.TemporaryDirectory() as temp:
   base=Path(temp);exe=base/'exe';exe.write_text('fake')
   tasks=[{'id':str(i),'configuration_id':'cfg','mode':'legacy','layout':'side-two','geometry':'mono16','events':0,'seeds':[1,2],'geometry_seeds':[3,4]} for i in range(3)]
   for t in tasks:
    f=base/t['id'];f.mkdir();(f/'run.mac').write_text('/run/initialize\n');t['macro_sha256']=study.digest(f/'run.mac')
   m={'phase':'geometry','tasks':tasks};(base/'manifest.json').write_text(json.dumps(m))
   def fail(*args,**kwargs):kwargs['stdout'].write('***** COMMAND NOT FOUND </bad> *****\n');return SimpleNamespace(returncode=0)
   with patch.object(runner,'validate_study_inputs',return_value=m),patch.object(runner.subprocess,'run',side_effect=fail) as process,patch.dict(os.environ,{'G4RUN_MANAGER_TYPE':'Serial'}),contextlib.redirect_stdout(io.StringIO()):
    with self.assertRaisesRegex(ValueError,'stopped'):runner.run(base,base,exe,workers=1)
    self.assertEqual(process.call_count,1)
   self.assertEqual(study.read_json(base/'0/receipt.json')['exit_code'],0)
   self.assertEqual(study.read_json(base/'0/receipt.json')['status'],'failed')
   self.assertFalse((base/'1/simulation.log').exists())
 def test_two_workers_keep_inflight_receipts_but_stop_new_dispatch(self):
  with tempfile.TemporaryDirectory() as temp:
   base=Path(temp);exe=base/'exe';exe.write_text('fake');tasks=[];barrier=threading.Barrier(2)
   for i in range(4):
    f=base/str(i);f.mkdir();(f/'run.mac').write_text('/run/initialize\n')
    tasks.append({'id':str(i),'configuration_id':'cfg','mode':'legacy','layout':'side-two','geometry':'mono16','events':0,'seeds':[1,2],'geometry_seeds':[3,4],'macro_sha256':study.digest(f/'run.mac')})
   m={'phase':'geometry','tasks':tasks};(base/'manifest.json').write_text(json.dumps(m))
   def process(*args,**kwargs):
    barrier.wait(timeout=2)
    if Path(kwargs['cwd']).name=='0':kwargs['stdout'].write('FatalException\n')
    else:time.sleep(.05);kwargs['stdout'].write('normal geometry\n')
    return SimpleNamespace(returncode=0)
   with patch.object(runner,'validate_study_inputs',return_value=m),patch.object(runner.subprocess,'run',side_effect=process) as calls,patch.dict(os.environ,{'G4RUN_MANAGER_TYPE':'Serial'}),contextlib.redirect_stdout(io.StringIO()):
    with self.assertRaisesRegex(ValueError,'stopped'):runner.run(base,base,exe,workers=2)
    self.assertEqual(calls.call_count,2)
   self.assertTrue((base/'0/receipt.json').exists() and (base/'1/receipt.json').exists())
   self.assertFalse((base/'2/simulation.log').exists())

if __name__=='__main__':unittest.main()
