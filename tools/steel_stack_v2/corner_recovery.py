#!/usr/bin/env python3
"""Finite local numerical-candidate campaign; never runs science or submits OSC."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import time

import numpy as np
import uproot
import run as infrastructure
from acceptance import compare_runs, single_file, registered_tasks
from audit import check_log, tree_arrays, HARD_ZERO, FATES, PHYSICS_STATE
from corner_cases import inputs, table, config, SCALES, LAYOUTS, TOLERANCE_MM
from model import require, canonical_json, configuration_hash

REPO=infrastructure.REPO
OLD=REPO/'outputs/steel_stack_v2/20260929T203903Z-infrastructure'
sha=infrastructure.sha
read=infrastructure.read
save=infrastructure.save
local=infrastructure.local

def prepare(batch):
    require(not (batch/'corner-registry.json').exists(),'Registry already exists')
    batch.mkdir(parents=True,exist_ok=True)
    infrastructure.preserve_before(batch)
    infrastructure.verify_analysis_environment(batch)
    old_tasks={t['task_id']:t for t in read(OLD/'tasks.json')}
    old_manifest=read(OLD/'candidate/source-manifest.json')
    before=read(batch/'reference/source-manifest.json')
    require(before['source_files']==old_manifest['source_files'],'Pre-repair frozen source differs from failed candidate')
    refs={}
    for name in ('source-manifest.json','binary.json','environment.json'):
        refs[name]={'path':str(OLD/'candidate'/name),'sha256':sha(OLD/'candidate'/name)}
    old_failed=old_tasks['observer-back-four-t24-g0500-on']
    refs['failed_root']={'path':str(single_file(old_failed['run_dir'],'outputs/*.root'))}
    refs['failed_root']['sha256']=sha(refs['failed_root']['path'])
    save(batch/'pre-repair-identity.json',refs)
    rows=inputs();save(batch/'ray-inputs.json',rows)
    jobs=[]
    for scale in SCALES:
        for layout in LAYOUTS:
            selected=[r for r in rows if r['layout']==layout]
            name=f'photons-s{scale:02d}-{layout}'
            source=single_file(old_tasks[f'smoke-{layout}-t24-g0500']['run_dir'],'macros/*.mac')
            job=dict(job_id=name,kind='photons',scale=scale,layout=layout,events=len(selected),
                     config=config(layout,scale),independent_science_sample=False,timeout_seconds=900,
                     template=str(source),template_sha256=sha(source))
            job['input_table']=str(batch/'inputs'/f'{name}.txt')
            Path(job['input_table']).parent.mkdir(parents=True,exist_ok=True)
            Path(job['input_table']).write_text(table(selected))
            job['input_sha256']=sha(job['input_table']);jobs.append(job)
    source=single_file(old_failed['run_dir'],'macros/*.mac')
    for scale in SCALES:
        jobs.append(dict(job_id=f'neutrons-s{scale:02d}',kind='neutrons',scale=scale,layout='back-four',events=2,
            config=config('back-four',scale),independent_science_sample=False,timeout_seconds=900,
            template=str(source),template_sha256=sha(source),seed1=old_failed['seed1'],seed2=old_failed['seed2'],
            reference_run_dir=old_failed['run_dir']))
    # Ordered replay first, then the complete optical matrix. Promotion is a
    # separate command: no further simulation is dispatched by audit.
    jobs.sort(key=lambda j:(j['kind']!='neutrons',SCALES.index(j['scale']),LAYOUTS.index(j['layout'])))
    for job in jobs:
        job['configuration_hash']=configuration_hash(job['config'])
        macro=Path(job['template']).read_text()
        additions=f"/opnovice2/numerics/mode {'legacy' if job['scale']==0 else 'painted-corner-v1'}\n/opnovice2/numerics/cornerScale {job['scale']}\n"
        if job['kind']=='photons': additions+=f"/opnovice2/numerics/probeFile {local.cpath(job['input_table'])}\n"
        macro=macro.replace('/run/initialize',additions+'/run/initialize')
        macro=re.sub(r'^/run/beamOn .*$',f"/run/beamOn {job['events']}",macro,flags=re.M)
        # Runtime output is fixed before execution, not edited after freezing.
        run_dir=batch/'corner-runs'/job['job_id']/'attempt-001'
        macro=re.sub(r'^/analysis/setFileName .*$',f'/analysis/setFileName {local.cpath(run_dir/"outputs/result")}',macro,flags=re.M)
        path=batch/'macros'/f"{job['job_id']}.mac";path.parent.mkdir(exist_ok=True);path.write_text(macro)
        job.update(macro=str(path),macro_sha256=sha(path),run_dir=str(run_dir))
    assert sum(j['events'] for j in jobs if j['kind']=='photons')==10560
    assert sum(j['events'] for j in jobs if j['kind']=='neutrons')==8
    save(batch/'corner-registry.json',dict(schema_version='painted-corner-validation-v1',jobs=jobs,
        ray_input_sha256=sha(batch/'ray-inputs.json'),neutron_budget=130,photon_budget=10560,
        preflight_neutrons=8,remaining_acceptance_new_neutrons=122,timeout_seconds=900,
        scales=list(SCALES),surface_tolerance_mm=TOLERANCE_MM,promotion_rule='16/32 ->16; otherwise 32/64 ->32; else stop'))
    save(batch/'corner-registry-lock.json',dict(sha256=sha(batch/'corner-registry.json')))
    save(batch/'acceptance-template.json',registered_tasks())
    print('Preregistered 10,560 optical events, 8 neutron replays; 122 new acceptance events remain conditional.')

def registry(batch):
    require(sha(batch/'corner-registry.json')==read(batch/'corner-registry-lock.json')['sha256'],'Registry changed')
    document=read(batch/'corner-registry.json')
    require(sha(batch/'ray-inputs.json')==document['ray_input_sha256'],'Ray registration changed')
    for job in document['jobs']:
        for field in ('macro','template'):
            require(sha(job[field])==job[field+'_sha256'],f'{field} changed')
        if 'input_table' in job:require(sha(job['input_table'])==job['input_sha256'],'Input table changed')
    return document

def verify_controller(batch):
    frozen=batch/'candidate/source/tools/steel_stack_v2'
    for name in ('corner_recovery.py','corner_audit.py','corner_cases.py','run.py','model.py','audit.py','acceptance.py'):
        require(sha(Path(__file__).parent/name)==sha(frozen/name),'Run the frozen campaign controller: '+name)

def execute(batch,job_id=None):
    document=registry(batch);verify_controller(batch)
    infrastructure.verify_analysis_environment(batch)
    binary=infrastructure.verify_binary(batch,'candidate')
    for job in document['jobs']:
        if job_id and job['job_id']!=job_id:continue
        directory=Path(job['run_dir']);receipt=directory/'receipt.json'
        if receipt.exists():
            prior=read(receipt)
            require(prior.get('completed'),f"Inspect failed attempt; no automatic replay: {job['job_id']}")
            require(prior['binary_sha256']==binary['sha256'] and prior['registry_sha256']==sha(batch/'corner-registry.json'),'Identity changed')
            for name,digest in prior['artifacts'].items():require(sha(directory/name)==digest,'Result changed')
            continue
        require(not directory.exists(),'Existing partial attempt requires explicit review')
        (directory/'outputs').mkdir(parents=True);(directory/'logs').mkdir();(directory/'macros').mkdir()
        (directory/'macros/input.mac').write_bytes(Path(job['macro']).read_bytes())
        receipt_data=dict(job_id=job['job_id'],events=job['events'],kind=job['kind'],completed=False,
            accepted_for_science=False,registry_sha256=sha(batch/'corner-registry.json'),binary_sha256=binary['sha256'],
            source_manifest_sha256=sha(batch/'candidate/source-manifest.json'),started_utc=infrastructure.stamp())
        save(receipt,receipt_data);start=time.monotonic()
        print(f"START {job['job_id']}: {job['events']} events",flush=True)
        try:
            local.dexec(['timeout','--signal=TERM','--kill-after=30s','900s',local.cpath(binary['path']),local.cpath(job['macro'])],
                env={'G4RUN_MANAGER_TYPE':'Serial','DISPLAY':'','UPDATE_LATEST':'0'},
                workdir=local.cpath(batch/'candidate/source/test/OpNovice2'),log=directory/'logs/run.log')
            check_log(directory/'logs/run.log',job['events'])
            with uproot.open(directory/'outputs/result.root') as root:
                require(root['scan'].num_entries==job['events'],'Incomplete event count')
            if job['kind']=='neutrons' and job['scale']==0:
                comparison=compare_runs(job['reference_run_dir'],directory)
                with uproot.open(single_file(job['reference_run_dir'],'outputs/*.root')) as old, uproot.open(directory/'outputs/result.root') as new:
                    for name in ('stack_event_v2','stack_layer_v2','stack_sensor_v2','stack_photon_flow_v2'):
                        require(list(old[name].keys())==list(new[name].keys()),'Legacy v2 tree schema changed')
                        for field in old[name].keys():
                            a,b=old[name][field].array(library='np'),new[name][field].array(library='np')
                            require(np.array_equal(a,b,equal_nan=a.dtype.kind in 'fc'),f'Frozen replay differs: {name}.{field}')
                save(directory/'exact-replay.json',comparison)
            receipt_data.update(completed=True,elapsed_seconds=time.monotonic()-start,finished_utc=infrastructure.stamp(),
                artifacts={str(p.relative_to(directory)):sha(p) for p in directory.rglob('*') if p.is_file() and p!=receipt})
            save(receipt,receipt_data)
            print(f"COMPLETE {job['job_id']} {receipt_data['elapsed_seconds']:.1f}s",flush=True)
        except Exception as exc:
            receipt_data.update(error=str(exc),elapsed_seconds=time.monotonic()-start,finished_utc=infrastructure.stamp())
            save(receipt,receipt_data);raise

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','run','audit'])
    p.add_argument('--batch-dir',required=True);p.add_argument('--job-id');a=p.parse_args()
    batch=infrastructure.checked_batch(a.batch_dir)
    if a.command=='prepare':prepare(batch)
    elif a.command=='run':execute(batch,a.job_id)
    else:
        from corner_audit import audit
        report=audit(batch);save(batch/'corner-promotion.json',report)
        print(json.dumps(report,indent=2))
        if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
