#!/usr/bin/env python3
"""Run fresh independent task processes with bounded concurrency and fail-stop dispatch."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import json
import os
from pathlib import Path
import subprocess
import time
from study import *
from validation import validate_inputs as validate_study_inputs, log_failures


def run(campaign,repo,executable,qualified_receipt=None,timeout=900,workers=1):
    campaign,repo,executable=map(lambda p:Path(p).resolve(),(campaign,repo,executable))
    require(workers in (1,2),'workers must be 1 or 2');require(timeout>0,'timeout must be positive')
    if read_json(campaign/'manifest.json')['phase']=='probe':
        from probes import validate_inputs
    else:validate_inputs=validate_study_inputs
    m=validate_inputs(campaign,repo);exe_hash=digest(executable);qualification=None
    if m['phase']!='geometry':
        require(qualified_receipt is not None,'qualified receipt required')
        q=read_json(qualified_receipt);require(q['status']=='passed','qualification failed')
        core_sources={p:h for p,h in m['source_files_sha256'].items() if p!='tools/combined_layouts/probes.py'}
        require(q['source_files_sha256']==core_sources,'qualification source inventory differs')
        require(q['executable_sha256']==exe_hash,'qualification executable differs')
        if m['phase']=='probe':
            require(q['phase']=='smoke','probes require smoke audit')
            require(m['base_smoke']['manifest_sha256']==q['manifest_sha256'],'probe base differs from qualifying smoke')
        else:
            require(q['configurations']==m['configurations'],'qualification mode/layout/geometry matrix differs')
            if m['phase']=='smoke':require(q['phase']=='geometry','smoke requires geometry audit')
            else:
                require(q.get('geometry_status')==q.get('smoke_status')==q.get('physics_probes_status')==q.get('regression_status')=='passed','exploration requires geometry, smoke and probe qualification')
        qualification={'path':str(Path(qualified_receipt).resolve()),'sha256':digest(qualified_receipt)}
    for t in m['tasks']:
        f=campaign/t['id'];require(not any((f/n).exists() for n in ('receipt.json','simulation.log','result.root','result_summary.csv')),'prior task execution: '+t['id'])
    require(os.environ.get('G4RUN_MANAGER_TYPE')=='Serial','set G4RUN_MANAGER_TYPE=Serial')
    write_json(campaign/'execution.json',{'manifest_sha256':digest(campaign/'manifest.json'),'executable_sha256':exe_hash,
       'executable_path':str(executable),'qualification':qualification,'workers':workers,'timeout_seconds':timeout,
       'run_manager':'Serial','failure_policy':'stop dispatch on first observed failed task; retain in-flight receipts; no retries',
       'runtime_environment':{k:v for k,v in os.environ.items() if k.startswith('G4') or k=='GEANT4_DATA_DIR'}})
    execution_hash=digest(campaign/'execution.json');manifest_hash=digest(campaign/'manifest.json')

    def execute_task(t):
        folder=campaign/t['id'];start=time.monotonic();error=None;code=None
        # Inputs were validated before dispatch; detect last-moment macro/binary drift before launching.
        require(digest(folder/'run.mac')==t['macro_sha256'] and digest(executable)==exe_hash,'inputs changed before process launch')
        with (folder/'simulation.log').open('x') as log:
            try:
                completed=subprocess.run([str(executable),'run.mac'],cwd=folder,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
                code=completed.returncode
            except subprocess.TimeoutExpired:error='timeout; no automatic retry';code=124
            except OSError as exc:error=f'process launch failed: {exc}'
        failures=log_failures((folder/'simulation.log').read_text(errors='replace'))
        missing=[name for name in ('result.root','result_summary.csv') if t['events'] and (not (folder/name).is_file() or (folder/name).stat().st_size==0)]
        observed_hash=digest(executable)
        if observed_hash!=exe_hash:failures.append('executable changed during process')
        r={'status':'passed' if code==0 and not error and not failures and not missing else 'failed','exit_code':code,
           'error':error,'ui_failure':failures,'missing_or_empty_outputs':missing,'elapsed_seconds':time.monotonic()-start,
           'manifest_sha256':manifest_hash,'execution_sha256':execution_hash,'executable_sha256':observed_hash,
           'macro_sha256':digest(folder/'run.mac'),'log_sha256':digest(folder/'simulation.log'),'qualification':qualification,
           'configuration_id':t['configuration_id'],'mode':t['mode'],'layout':t['layout'],'geometry':t['geometry'],
           'events_requested':t['events'],'seeds':t['seeds'],'geometry_seeds':t['geometry_seeds']}
        write_json(folder/'receipt.json',r);return r

    next_task=0;failed=False;errors=[];completed_ids=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={}
        def dispatch():
            nonlocal next_task
            while not failed and len(pending)<workers and next_task<len(m['tasks']):
                task=m['tasks'][next_task];next_task+=1
                print(json.dumps({'starting_task':task['id'],'events':task['events']}),flush=True)
                pending[pool.submit(execute_task,task)]=task
        dispatch()
        while pending:
            done,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in done:
                task=pending.pop(future)
                try:
                    r=future.result();completed_ids.append(task['id'])
                    print(json.dumps({'task':task['id'],**r}),flush=True)
                    if r['status']!='passed':failed=True;errors.append(task['id'])
                except Exception as exc:
                    failed=True;errors.append(task['id']+': '+str(exc))
                    print(json.dumps({'task':task['id'],'status':'failed-before-receipt','error':str(exc)}),flush=True)
            dispatch()
    write_json(campaign/'run-completion.json',{'status':'failed' if failed else 'passed','completed_task_ids':completed_ids,
               'not_started_task_ids':[t['id'] for t in m['tasks'][next_task:]],'errors':errors,'automatic_retries':0})
    require(not failed,'campaign stopped after failed task: '+str(errors))
    validate_inputs(campaign,repo);require(digest(executable)==exe_hash,'executable changed after campaign')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',type=Path,required=True);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--executable',type=Path,required=True);p.add_argument('--qualified-receipt',type=Path)
    p.add_argument('--timeout',type=int,default=900);p.add_argument('--workers',type=int,choices=(1,2),default=1)
    a=p.parse_args();run(a.campaign,a.repo,a.executable,a.qualified_receipt,a.timeout,a.workers)

if __name__=='__main__':main()
