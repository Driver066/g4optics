#!/usr/bin/env python3
"""Compute-node optical qualification, separate from performance/science samples."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

from corner_cases import inputs, table, config, SCALES, LAYOUTS
from osc import sha256, require, validate_source

def save(path,value):path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')

def prepare(campaign):
    build=json.loads((campaign/'remote-build.json').read_text());source=Path(build['source_root_remote'])
    validate_source(campaign/'source-manifest.json',source)
    root=campaign/'qualification';require(not root.exists(),'Qualification inputs already exist')
    root.mkdir();(root/'inputs').mkdir();(root/'macros').mkdir();(root/'runs').mkdir()
    rows=inputs();save(root/'ray-inputs.json',rows)
    templates={}
    for layout in LAYOUTS:
        pointer=root/(layout+'.pointer')
        env=dict(os.environ,DRY_RUN='1',UPDATE_LATEST='0',PLOT_WITH_ROOT='0',PYTHONDONTWRITEBYTECODE='1',
            OPNOVICE2_EXECUTABLE=build['executable']['path'],SCAN_RUNS_DIR=str(root/'templates'/layout),
            SCAN_RESULT_POINTER=str(pointer),SCAN_GIT_COMMIT=json.loads((campaign/'source-manifest.json').read_text())['git_commit'],
            SCAN_GIT_BRANCH='frozen-osc-qualification',SCAN_GIT_DIRTY='false')
        args=['bash',str(source/'test/OpNovice2/run_sipm_cavity_scan.sh'),'full','custom','--study-preset','steel-module-stack-v2',
            '--sipm-layout',layout,'--tile-thickness-mm','24','--readout-gap-mm','0.5','--events','2',
            '--x-min','0','--x-max','0','--y-min','0','--y-max','0','--step','1','--grid-unit','mm',
            '--seed1','121845476','--seed2','1133654031','--no-root-plots','--dry-run']
        with (root/(layout+'-template.log')).open('x') as log:subprocess.run(args,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        generated=list((Path(pointer.read_text().strip())/'macros').glob('*.mac'));require(len(generated)==1,'Missing template')
        templates[layout]=generated[0]
    jobs=[]
    for scale in SCALES:
        for layout in LAYOUTS:
            selected=[r for r in rows if r['layout']==layout]
            name=f'photons-s{scale:02d}-{layout}'
            path=root/'inputs'/(name+'.txt');path.write_text(table(selected))
            job=dict(job_id=name,kind='photons',scale=scale,layout=layout,events=len(selected),
                config=config(layout,scale,'painted-corner-v2'),input_table=str(path),input_sha256=sha256(path))
            jobs.append(job)
        jobs.append(dict(job_id=f'neutrons-s{scale:02d}',kind='neutrons',scale=scale,layout='back-four',events=2,
            config=config('back-four',scale,'painted-corner-v2')))
    for job in jobs:
        directory=root/'runs'/job['job_id'];(directory/'outputs').mkdir(parents=True);(directory/'logs').mkdir()
        macro=templates[job['layout']].read_text()
        mode='legacy' if job['scale']==0 else 'painted-corner-v2'
        macro=re.sub(r'^/opnovice2/numerics/mode .*$', '/opnovice2/numerics/mode '+mode,macro,flags=re.M)
        macro=re.sub(r'^/opnovice2/numerics/cornerScale .*$', '/opnovice2/numerics/cornerScale '+str(job['scale']),macro,flags=re.M)
        if job['kind']=='photons':
            macro=macro.replace('/run/initialize',f"/opnovice2/numerics/probeFile /qual/inputs/{job['job_id']}.txt\n/run/initialize")
        macro=re.sub(r'^/analysis/setFileName .*$',f"/analysis/setFileName /qual/runs/{job['job_id']}/outputs/result",macro,flags=re.M)
        macro=re.sub(r'^/run/beamOn .*$',f"/run/beamOn {job['events']}",macro,flags=re.M)
        path=root/'macros'/(job['job_id']+'.mac');path.write_text(macro)
        job.update(macro=str(path),macro_sha256=sha256(path),run_dir=str(directory))
    save(root/'registry.json',dict(jobs=jobs,photon_events=10560,neutron_events=8,
        purpose='cross-architecture-engineering-not-science',ray_inputs_sha256=sha256(root/'ray-inputs.json'),
        build_receipt_sha256=sha256(campaign/'remote-build.json'),prepare_implementation_sha256=sha256(__file__)))
    (root/'registry.sha256').write_text(sha256(root/'registry.json')+'\n')
    print('Prepared 10,560 optical events and 8 neutron checks; no submission')

def execute(campaign):
    require(os.environ.get('SLURM_JOB_ID'),'Qualification runs only inside a compute allocation')
    root=campaign/'qualification';require(sha256(root/'registry.json')==(root/'registry.sha256').read_text().strip(),'Registry changed')
    registry=json.loads((root/'registry.json').read_text());require(sha256(campaign/'remote-build.json')==registry['build_receipt_sha256'],'Build receipt changed')
    build=json.loads((campaign/'remote-build.json').read_text());source=Path(build['source_root_remote'])
    validate_source(campaign/'source-manifest.json',source)
    for field in ('executable','sif','dataset_receipt'):
        require(sha256(build[field]['path'])==build[field]['sha256'],'Frozen '+field+' changed')
    catalog=json.loads((source/'tools/local/geant4-11.4.2-datasets.json').read_text())
    environment={e['env']:'/g4data/'+e['directory'] for e in catalog['datasets']}
    environment.update(G4RUN_MANAGER_TYPE='Serial',OMP_NUM_THREADS='1',DISPLAY='')
    shell='set -euo pipefail; source /opt/geant4/bin/geant4.sh; export '+' '.join(shlex.quote(k+'='+v) for k,v in environment.items())+'; exec "$@"'
    prefix=['apptainer','exec','--cleanenv','--bind',str(source)+':/work/g4optics:ro','--bind',build['data_root_remote']+':/g4data:ro',
        '--bind',str(Path(build['executable']['path']).parent)+':/opt/frozen:ro','--bind',str(root)+':/qual',
        build['sif']['path'],'bash','-c',shell,'qualify']
    from audit import check_log
    from corner_audit import inspect_job
    for job in registry['jobs']:
        require(sha256(job['macro'])==job['macro_sha256'],'Macro changed')
        if job['kind']=='photons':require(sha256(job['input_table'])==job['input_sha256'],'Probe table changed')
        directory=Path(job['run_dir']);receipt_path=directory/'receipt.json'
        require(not receipt_path.exists(),'Preserve previous execution; use a new qualification attempt')
        receipt=dict(completed=False,accepted_for_science=False,events=job['events'],scheduler_job=os.environ['SLURM_JOB_ID'],
                     build_receipt_sha256=registry['build_receipt_sha256'],registry_sha256=sha256(root/'registry.json'))
        save(receipt_path,receipt);start=time.monotonic();print('START',job['job_id'],flush=True)
        try:
            with (directory/'logs/run.log').open('x') as log:
                subprocess.run(prefix+['/opt/frozen/'+Path(build['executable']['path']).name,'/qual/macros/'+job['job_id']+'.mac'],
                    stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
            check_log(directory/'logs/run.log',job['events'])
            receipt.update(completed=True,elapsed_seconds=time.monotonic()-start,
                artifacts={str(p.relative_to(directory)):sha256(p) for p in directory.rglob('*') if p.is_file() and p!=receipt_path})
            save(receipt_path,receipt);inspect_job(job)
            print('COMPLETE',job['job_id'],flush=True)
        except Exception as exc:
            receipt.update(completed=False,error=str(exc),elapsed_seconds=time.monotonic()-start);save(receipt_path,receipt);raise
    report=assess(root,registry);save(root/'qualification-result.json',report)
    require(report['passed'],'Remote numerical qualification failed; do not start benchmarks/science')
    print(json.dumps(report,indent=2))

def assess(root,registry):
    from corner_audit import inspect_job
    rows={r['probe_id']:r for r in json.loads((root/'ray-inputs.json').read_text())}
    photons={s:{} for s in SCALES};neutrons={};headers=[]
    for job in registry['jobs']:
        result=inspect_job(job);headers.append(result['header']['post_step_order'])
        if job['kind']=='photons':
            for event in result['events'].values():photons[job['scale']][event['id']]=event
        else:neutrons[job['scale']]=result['events']
    require(all(h==headers[0] for h in headers),'Process ordering changed')
    require(all(set(p)==set(rows) for p in photons.values()),'Missing ray coverage')
    reproduced=any(e['end']['boundary_no_rindex'] or e['end']['painted_zero_step_escapes']
        for key,e in photons[0].items() if rows[key]['family']=='captured')
    failures=[]
    if not reproduced:failures.append('Captured legacy ray did not reproduce its defect on this architecture')
    for scale in (16,32,64):
        for key,event in photons[scale].items():
            old=photons[0][key]
            if rows[key]['family'].startswith('control-'):
                if event['end']['corrections'] or any(event[k]!=old[k] for k in ('physical','sequence','terminal')) or event['end']['rng_end']!=old['end']['rng_end']:
                    failures.append(f'{scale}/{key}: negative control changed')
            elif event['end']['boundary_no_rindex'] or event['end']['painted_zero_step_escapes'] or any(t[5][0] in ('World','SteelAbsorber','outside') for t in event['terminal']):
                failures.append(f'{scale}/{key}: target transport failed')
        if any(e['end']['boundary_no_rindex'] or e['end']['painted_zero_step_escapes'] for e in neutrons[scale].values()):failures.append(f'{scale}: neutron transport failed')
    for a,b in ((16,32),(32,64)):
        for key in rows:
            if any(photons[a][key][field]!=photons[b][key][field] for field in ('sequence','terminal')):
                failures.append(f'{a}/{b}/{key}: scale disagreement')
    return dict(passed=not failures,failures=failures,architecture='x86_64',profile='painted-corner-v2',scale=16,
        optical_events=10560,neutron_events=8,legacy_ray_reproduced=reproduced,negative_controls_per_scale=256,
        local_arm64_event_equality_required=False,scientific_events=0,assessor_sha256=sha256(__file__))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','run'])
    p.add_argument('--campaign-dir',required=True,type=Path);a=p.parse_args()
    (prepare if a.command=='prepare' else execute)(a.campaign_dir.resolve())
