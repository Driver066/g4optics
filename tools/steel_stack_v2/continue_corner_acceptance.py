#!/usr/bin/env python3
"""Prepare the complete acceptance matrix after a verified numerical promotion.

Copy immutable build/source identities and verified historical reference runs;
never execute or submit tasks here. The existing frozen run.py does execution.
"""
import argparse
import json
from pathlib import Path
import shutil

import run as infra
from acceptance import registered_tasks
from corner_audit import audit as audit_corner
from model import make_configuration, require, canonical_json

def import_build(source,destination,bindings=()):
    infra.verify_source(source)
    require(not destination.exists(),'Build destination already exists')
    destination.mkdir(parents=True)
    shutil.copytree(source/'source',destination/'source')
    for name in ('source-manifest.json','environment.json'):
        shutil.copy2(source/name,destination/name)
    original=infra.read(source/'binary.json')
    require(infra.sha(original['path'])==original['sha256'],'Imported binary changed')
    require(infra.sha(source/'build/CMakeCache.txt')==original['cmake_cache_sha256'],'Imported build cache changed')
    source_hash=infra.sha(source/'source-manifest.json')
    explicit=original.get('source_manifest_sha256')
    if explicit is None:
        require(bindings,'Historical binary lacks direct source identity and has no verified run receipts')
        expected_home='CMAKE_HOME_DIRECTORY:INTERNAL='+infra.local.cpath(source/'source/test/OpNovice2')
        require(expected_home in (source/'build/CMakeCache.txt').read_text().splitlines(),'Historical build source path differs')
        for path in bindings:
            binding=infra.read(path)
            require(binding.get('accepted') is True and binding.get('source_manifest_sha256')==source_hash
                    and binding.get('executable_sha256')==original['sha256'],'Historical run does not bind source and binary')
    else:
        require(source_hash==explicit,'Imported source identity changed')
    build=destination/'build';build.mkdir()
    shutil.copy2(original['path'],build/'OpNovice2')
    shutil.copy2(source/'build/CMakeCache.txt',build/'CMakeCache.txt')
    imported=dict(original,path=str(build/'OpNovice2'),imported_from=str(source),
                  source_manifest_sha256=source_hash,source_binding_method='original-binary-receipt' if explicit else 'verified-run-receipts-and-original-CMake-source-path',
                  source_binding_receipts=[{'path':str(p),'sha256':infra.sha(p)} for p in bindings],
                  original_binary_receipt_sha256=infra.sha(source/'binary.json'))
    infra.save(destination/'binary.json',imported)
    infra.verify_source(destination)

def prepare(promotion_batch,batch,reference_batch):
    require(not batch.exists(),'Use a new acceptance batch')
    report=infra.read(promotion_batch/'corner-promotion.json')
    verified=audit_corner(promotion_batch)
    require(canonical_json(report)==canonical_json(verified) and report['passed'] is True,'Numerical promotion is missing, changed, or failed')
    scale=report['selected_scale'];require(scale in (16,32),'Unregistered promoted scale')
    profile=infra.read(promotion_batch/'corner-registry.json')['candidate_profile']
    require(profile=='painted-corner-v2','This continuation requires the immediate-restoration v2 profile')
    old={t['task_id']:t for t in infra.read(reference_batch/'tasks.json')}
    references=[t for t in registered_tasks() if t['role']=='reference']
    for task in references:
        recorded=old[task['task_id']]
        infra.verify_completed(recorded,reference_batch)
        for key in ('events','seed1','seed2','preset','layout','tile_thickness_mm'):
            require(task[key]==recorded[key],'Historical reference input differs: '+key)
    batch.mkdir(parents=True);infra.preserve_before(batch);infra.verify_analysis_environment(batch)
    import_build(reference_batch/'reference',batch/'reference',[Path(old[t['task_id']]['receipt']) for t in references])
    import_build(promotion_batch/'candidate',batch/'candidate')
    tasks=registered_tasks()
    for task in tasks:
        if task['preset']=='steel-module-stack-v2':
            task['config']=make_configuration(task['layout'],task['tile_thickness_mm'],task['readout_gap_mm'],profile,scale)
        task.update(block=0,independent_sample=False,reproducibility_check=bool(task['compare_to']))
    infra.save(batch/'registered-tasks.json',tasks)
    manifest=dict(schema_version='steel-stack-v2-local-batch-v1',created_utc=infra.stamp(),matrix='acceptance',
        purpose='engineering-acceptance-not-scientific-evidence',execution='local-serial',geant4_version='11.4.2',
        update_latest=False,task_count=len(tasks),event_count=sum(t['events'] for t in tasks),
        imported_reference_events=8,new_event_count=122,task_timeout_seconds=900,
        optical_numerics={'profile':profile,'scale':scale},promotion_batch=str(promotion_batch),
        promotion_report_sha256=infra.sha(promotion_batch/'corner-promotion.json'),
        registered_tasks_sha256=infra.sha(batch/'registered-tasks.json'))
    infra.save(batch/'manifest.json',manifest)
    infra.save(batch/'promotion-evidence.json',report)
    for task in tasks:
        if task['role']!='reference':continue
        prior=old[task['task_id']];attempt=batch/'runs'/task['task_id']/'import-001'
        attempt.mkdir(parents=True);run_dir=attempt/'archived-run'
        shutil.copytree(prior['run_dir'],run_dir)
        audit_path=attempt/'audit.json';shutil.copy2(prior['audit_path'],audit_path)
        receipt=dict(accepted=True,role='reference',task_id=task['task_id'],events=task['events'],run_dir=str(run_dir),
            origin='verified-historical-reference-import-not-a-new-simulation',original_run_dir=prior['run_dir'],
            original_receipt=prior['receipt'],original_receipt_sha256=infra.sha(prior['receipt']),
            registered_task_sha256=infra.registered_task_hash(batch,task['task_id']),
            manifest_sha256=infra.sha(batch/'manifest.json'),source_manifest_sha256=infra.sha(batch/'reference/source-manifest.json'),
            executable_sha256=infra.read(batch/'reference/binary.json')['sha256'],
            audit_path=str(audit_path),audit_sha256=infra.sha(audit_path),artifacts=infra.artifact_hashes(run_dir))
        receipt_path=attempt/'receipt.json';infra.save(receipt_path,receipt)
        task.update(accepted=True,run_dir=str(run_dir),receipt=str(receipt_path),receipt_sha256=infra.sha(receipt_path),
                    audit_path=str(audit_path),accounting_enabled=False)
        infra.verify_completed(task,batch)
    infra.save(batch/'tasks.json',tasks)
    infra.journal(batch,'acceptance-prepared-after-numerical-promotion',profile=profile,scale=scale,
                  imported_reference_events=8,new_events=122)
    print(json.dumps(dict(batch=str(batch),profile=profile,scale=scale,tasks=89,total_engineering_events=130,
                         verified_imported_reference_events=8,new_events_to_run=122),indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--promotion-batch',required=True)
    parser.add_argument('--batch-dir',required=True)
    parser.add_argument('--reference-batch',required=True)
    args=parser.parse_args()
    prepare(infra.checked_batch(args.promotion_batch),infra.checked_batch(args.batch_dir),infra.checked_batch(args.reference_batch))
