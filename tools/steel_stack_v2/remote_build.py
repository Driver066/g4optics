#!/usr/bin/env python3
"""Freeze and build an OSC x86_64 candidate; never run events or submit jobs."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys

from osc import sha256, require, validate_source, require_x86_elf, BUILD_SCHEMA

def save(path,value):
    path.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')

def build(campaign,expected_commit,sif,data_root,jobs):
    require(platform.system()=='Linux' and platform.machine()=='x86_64','Build on OSC Linux/x86_64')
    require(1<=jobs<=4,'Use 1-4 explicitly bounded compile jobs')
    campaign=campaign.resolve();checkout=campaign/'checkout';source=campaign/'source'
    require(not source.exists() and not (campaign/'remote-build.json').exists(),'Use a fresh build campaign; preserve existing attempts')
    head=subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(checkout),'status','--porcelain'],text=True)
    require(head==expected_commit and not dirty,'Checkout identity differs or contains uncommitted changes')
    names=subprocess.check_output(['git','-C',str(checkout),'ls-files','-z']).decode().split('\0')
    source.mkdir();files={}
    for name in filter(None,names):
        incoming=checkout/name;outgoing=source/name
        require(incoming.is_file() and not incoming.is_symlink(),'Frozen input must be a regular file: '+name)
        outgoing.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(incoming,outgoing);files[name]=sha256(outgoing)
    manifest=campaign/'source-manifest.json'
    save(manifest,dict(schema_version='steel-stack-v2-frozen-source-v1',created_utc=datetime.now(timezone.utc).isoformat(),
        git_commit=head,git_status='',source_files=files))
    validate_source(manifest)
    catalog=source/'tools/local/geant4-11.4.2-datasets.json'
    with (campaign/'dataset-verification.log').open('x') as log:
        subprocess.run([sys.executable,str(source/'tools/local/install_datasets.py'),str(data_root),'--verify-only',
            '--manifest',str(catalog),'--report',str(campaign/'dataset-verification.json')],stdout=log,stderr=subprocess.STDOUT,check=True)
    require(json.loads((campaign/'dataset-verification.json').read_text())['status']=='ok','Dataset verification failed')
    image_dir=campaign/'image';image_dir.mkdir();image=image_dir/'geant4.sif'
    original_image_hash=sha256(sif);shutil.copy2(sif,image)
    require(sha256(image)==original_image_hash,'Image copy changed')
    destination=campaign/'build';destination.mkdir()
    entries=json.loads(catalog.read_text())['datasets']
    env={e['env']:'/g4data/'+e['directory'] for e in entries}
    env.update(G4RUN_MANAGER_TYPE='Serial',OMP_NUM_THREADS='1',DISPLAY='',PYTHONDONTWRITEBYTECODE='1')
    shell='set -euo pipefail; source /opt/geant4/bin/geant4.sh; export '+ ' '.join(shlex.quote(k+'='+v) for k,v in env.items())+'; exec "$@"'
    prefix=['apptainer','exec','--cleanenv','--bind',str(source)+':/work/g4optics:ro',
        '--bind',str(destination)+':/build','--bind',str(data_root)+':/g4data:ro',str(image),'bash','-c',shell,'g4-build']
    def capture(args):return subprocess.check_output(prefix+args,text=True).strip()
    version=capture(['geant4-config','--version']);arch=capture(['uname','-m'])
    require(version=='11.4.2' and arch=='x86_64','Wrong actual container version/architecture')
    checked=capture(['geant4-config','--check-datasets'])
    require(len(checked.splitlines())==12 and all(' INSTALLED ' in line for line in checked.splitlines()),'Container datasets incomplete')
    environment=dict(geant4_version=version,architecture=arch,datasets=checked,
        compiler=capture(['g++','--version']),cmake=capture(['cmake','--version']),host=platform.node(),image_sha256=original_image_hash)
    save(campaign/'build-environment.json',environment)
    with (campaign/'build.log').open('x') as log:
        for args in (['cmake','-S','/work/g4optics/test/OpNovice2','-B','/build','-DCMAKE_BUILD_TYPE=Release',
                      '-DWITH_GEANT4_UIVIS=OFF','-DCMAKE_PREFIX_PATH=/opt/geant4'],['cmake','--build','/build','-j',str(jobs)]):
            log.write(shlex.join(prefix+args)+'\n');log.flush()
            subprocess.run(prefix+args,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
    executable=destination/'OpNovice2';require_x86_elf(executable)
    libraries=capture(['ldd','/build/OpNovice2']);require('not found' not in libraries and 'libG4' in libraries,'Incomplete Geant4 linkage')
    (campaign/'dynamic-libraries.txt').write_text(libraries+'\n')
    validate_source(manifest)
    receipt=data_root/'geant4-datasets-receipt.json'
    result=dict(schema_version=BUILD_SCHEMA,mock=False,os='linux',architecture='x86_64',geant4_version=version,
        run_manager='Serial',source_manifest_sha256=sha256(manifest),source_root_remote=str(source),data_root_remote=str(data_root),
        executable=dict(path=str(executable),remote_path=str(executable),sha256=sha256(executable)),
        sif=dict(path=str(image),remote_path=str(image),sha256=sha256(image),architecture='x86_64',geant4_version=version),
        dataset_receipt=dict(path=str(receipt),sha256=sha256(receipt)),
        build_environment_sha256=sha256(campaign/'build-environment.json'),cmake_cache_sha256=sha256(destination/'CMakeCache.txt'),
        actual_event_runs=0,runtime_qualification='pending-compute-node-validation')
    save(campaign/'remote-build.json',result)
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign-dir',required=True,type=Path);p.add_argument('--expected-commit',required=True)
    p.add_argument('--sif',required=True,type=Path);p.add_argument('--data-root',required=True,type=Path)
    p.add_argument('--build-jobs',type=int,default=2);a=p.parse_args()
    build(a.campaign_dir,a.expected_commit,a.sif,a.data_root,a.build_jobs)
