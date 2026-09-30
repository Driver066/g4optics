"""Strict promotion gates; a diagnostic failure is never production acceptance."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import uproot
from audit import HARD_ZERO, FATES, PHYSICS_STATE, tree_arrays
from corner_cases import SCALES, TOLERANCE_MM
from model import canonical_json, require

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())

def inspect_job(job):
    directory=Path(job['run_dir']);receipt=read(directory/'receipt.json')
    require(receipt.get('completed') is True,'Incomplete diagnostic job: '+job['job_id'])
    for name,value in receipt['artifacts'].items():require(digest(directory/name)==value,'Diagnostic result changed: '+name)
    rows=[json.loads(line) for line in (directory/'outputs/result.optical-numerics.jsonl').read_text().splitlines()]
    require(rows and rows[0]['kind']=='run' and rows[-1]['kind']=='run_end','Incomplete numerical trace')
    header=rows[0];identity=header['identity'];scale=job['scale']
    require(identity['profile']==job['config']['optical_numerics']['profile'] and identity['scale']==scale,'Numerical profile differs')
    require(identity['surface_tolerance_mm']==TOLERANCE_MM,'Runtime tolerance differs from preregistration')
    require(identity['diagnostic_primary']==(job['kind']=='photons'),'Source classification differs')
    require(header['boundary_class']==('G4OpBoundaryProcess' if scale==0 else 'PaintedCornerBoundary'),'Wrong boundary class')
    physics=read(directory/'outputs/result.physics-state.json')
    require(physics==PHYSICS_STATE,'Actual physical process settings changed')
    by_event=defaultdict(list)
    for row in rows[1:-1]:by_event[row['event']].append(row)
    require(sorted(by_event)==list(range(job['events'])),'Missing/duplicate/out-of-range numerical events')
    events={};corrections=[]
    for event,records in by_event.items():
        ends=[r for r in records if r['kind']=='event_end'];require(len(ends)==1,'Repeated/missing event end')
        end=ends[0];require(end['unsettled']==0,'Unsettled numerical tracks')
        cs=[r for r in records if r['kind']=='correction'];corrections+=cs
        require(end['corrections']==len(cs),'Correction count differs')
        require(len({(r['track'],r['reflection_step']) for r in cs})==len(cs),'Repeated correction')
        for c in cs:
            immediate=identity['profile']=='painted-corner-v2'
            if immediate:
                require(c['raw_status']==7 and c['step']==c['reflection_step'],'Immediate correction is not its physical SpikeReflection')
                require(c.get('transport_cache_verified') is True and c.get('physical_proposals_delegated_unchanged') is True,'Immediate correction lacks transport/physics verification')
                require('raw_post' in c,'Original physical interface is missing')
            else:
                require(c['raw_status']==13 and c['step']==c['reflection_step']+1,'Correction is not next-step StepTooSmall')
            require(c['same_volume_navigator_verified'] is True and c['post']==c['next']==['Tank',c['tile']],'Relocation volume mismatch')
            require(c['faces'] in (3,5,6,7) and c['scale']==scale and scale>0,'Correction scope changed')
            require(c['particle_change_modified_fields']==(['position','geometry_state'] if immediate else ['position']),'Forbidden ParticleChange modification')
            before=np.asarray(c['before_mm']);after=np.asarray(c['after_mm']);distance=float(np.linalg.norm(after-before))
            require(0<distance<=math.sqrt(3)*(scale+1)*TOLERANCE_MM,'Displacement exceeds bound')
            require(abs(distance-c['displacement_mm'])<1.e-20,'Reported displacement differs')
        probes=[r for r in records if r['kind']=='probe']
        if job['kind']=='photons':require(len(probes)==1,'Missing/repeated probe ID')
        boundaries=[r for r in records if r['kind']=='boundary']
        terminals=[r for r in records if r['kind']=='terminal']
        # Strip only technical statuses. Preserve process, volume, and sensor
        # identities in every physical boundary/terminal entry, including descendants.
        sequence=[(r['track'],r['parent'],r['status'],r['pre'],r['post']) for r in boundaries if r['status'] not in (-1,0,11,12,13)]
        terminal=[(r['track'],r['parent'],r['sensor'],r['process'],r['status'],r['volume']) for r in terminals]
        events[event]=dict(id=probes[0]['id'] if probes else event,end=end,sequence=sequence,terminal=terminal,
            edge_reflections=sum(r['edge_reflection'] for r in boundaries),rows=records)
    with uproot.open(directory/'outputs/result.root') as root:
        ledger=tree_arrays(root['stack_event_v2'])
        require(len(ledger['event_id'])==job['events'] and np.array_equal(ledger['event_id'],np.arange(job['events'])),'Ledger event IDs differ')
        for name in HARD_ZERO:
            if name not in ('fate_no_rindex','boundary_no_rindex','collected_step_no_rindex'):
                require((ledger[name]==0).all(),'Ledger failure '+name)
        require(np.array_equal(ledger['births_total'],ledger['tracks_started']),'Birth/start mismatch')
        require(np.array_equal(ledger['births_total'],ledger['tracks_finalized']),'Birth/final mismatch')
        require(np.array_equal(ledger['births_total'],sum(ledger['fate_'+f] for f in FATES)),'Terminal ledger mismatch')
        require(np.array_equal(ledger['births_total'],ledger['births_primary']+ledger['births_nonoptical_parent']+ledger['births_optical_parent']),'Ancestry mismatch')
        for material in ('tile','steel'):
            require(np.allclose(ledger[f'{material}_nonoptical_edep_mev']+ledger[f'{material}_optical_edep_mev'],ledger[f'legacy_{material}_edep_mev'],rtol=1.e-9,atol=1.e-9),'Edep mismatch')
        sensor=tree_arrays(root['stack_sensor_v2'])
        require(np.isin(sensor['global_copy'],job['config']['active_sensor_copies']).all(),'Invalid sensor')
        totals=np.bincount(sensor['event_id'],weights=sensor['detected_all_origins'],minlength=job['events'])
        require(np.array_equal(totals,ledger['detected_legacy']),'Sensor/legacy collection mismatch')
        for event,data in events.items():
            require(data['end']['boundary_no_rindex']==int(ledger['boundary_no_rindex'][event]),'Raw NoRINDEX tally differs')
        # Exact physical per-event output comparison for non-target controls.
        trees={name:tree_arrays(root[name]) for name in ('scan','stack_event_v2','stack_layer_v2','stack_sensor_v2','stack_photon_flow_v2')}
        for event,data in events.items():
            physical={}
            for name,values in trees.items():
                ids=values.get('event_id')
                if ids is None:ids=np.arange(len(next(iter(values.values()))))
                physical[name]={k:v[ids==event].tolist() for k,v in values.items()}
            data['physical']=json.dumps(physical,sort_keys=True,allow_nan=True,separators=(',',':'))
    return dict(header=header,events=events,corrections=corrections)

def audit(batch):
    # Import here to avoid a controller/audit import cycle.
    from corner_recovery import registry
    doc=registry(batch);inputs=read(batch/'ray-inputs.json');probe_info={r['probe_id']:r for r in inputs}
    failures=[];all_jobs={};report_jobs=[]
    for job in doc['jobs']:
        try:
            result=inspect_job(job);all_jobs[job['job_id']]=result
            report_jobs.append(dict(job_id=job['job_id'],complete=True,events=job['events'],
                corrections=len(result['corrections']),edge_reflections=sum(e['edge_reflections'] for e in result['events'].values()),
                no_rindex=sum(e['end']['boundary_no_rindex'] for e in result['events'].values()),
                painted_zero_step_escapes=sum(e['end']['painted_zero_step_escapes'] for e in result['events'].values())))
        except (ValueError,KeyError,OSError) as exc:
            failures.append(job['job_id']+': '+str(exc));report_jobs.append(dict(job_id=job['job_id'],complete=False,error=str(exc)))
    if failures:return dict(passed=False,status='incomplete-or-structural-failure',failures=failures,jobs=report_jobs,selected_scale=None)
    order=all_jobs['neutrons-s00']['header']['post_step_order']
    require(all(r['header']['post_step_order']==order for r in all_jobs.values()),'OpBoundary execution order/activation changed')
    baseline=all_jobs['neutrons-s00'];baseline_summary=next(r for r in report_jobs if r['job_id']=='neutrons-s00')
    reproduced=baseline_summary['no_rindex']==1 and baseline_summary['painted_zero_step_escapes']>=1
    reproduced=reproduced and (Path(next(j['run_dir'] for j in doc['jobs'] if j['job_id']=='neutrons-s00'))/'exact-replay.json').is_file()
    if not reproduced:failures.append('Frozen neutron legacy control did not reproduce the captured failure exactly')
    photons={scale:{} for scale in SCALES}
    for job in doc['jobs']:
        if job['kind']=='photons':
            for value in all_jobs[job['job_id']]['events'].values():
                require(value['id'] not in photons[job['scale']],'Repeated diagnostic ray')
                photons[job['scale']][value['id']]=value
    require(all(set(v)==set(probe_info) for v in photons.values()),'Optical test coverage differs')
    captured=[e for key,e in photons[0].items() if probe_info[key]['family']=='captured']
    optical_reproduced=any(e['end']['painted_zero_step_escapes'] for e in captured)
    if not optical_reproduced:failures.append('Captured single-photon family did not reproduce a zero-step painted escape')
    candidates={}
    for scale in SCALES[1:]:
        bad=[];non_target=0
        neutron=all_jobs[f'neutrons-s{scale:02d}']
        if any(e['end']['boundary_no_rindex'] or e['end']['painted_zero_step_escapes'] for e in neutron['events'].values()):bad.append('Original neutron case still fails NoRINDEX/painted escape')
        for key,current in photons[scale].items():
            old=photons[0][key];control=probe_info[key]['family'].startswith('control-')
            if control:
                non_target+=1
                if (current['end']['corrections'] or current['end']['rng_end']!=old['end']['rng_end']
                    or current['physical']!=old['physical'] or current['sequence']!=old['sequence'] or current['terminal']!=old['terminal']):bad.append(key+': non-target physics/RNG changed')
            elif current['end']['painted_zero_step_escapes'] or current['end']['boundary_no_rindex']:
                bad.append(key+': target still leaks or reaches NoRINDEX')
            elif any(t[5][0] in ('World','SteelAbsorber','outside') for t in current['terminal']):
                # With this fixture's opaque paint, a tile-born target photon
                # cannot legally finish in air/steel. Catch escapes absorbed in
                # air before they could trigger NoRINDEX at steel.
                bad.append(key+': target terminates outside tile/SiPM despite opaque paint')
        candidates[scale]=dict(passed=not bad,failures=bad,non_target_controls=non_target)
    stability=[];selected=None
    for left,right in ((16,32),(32,64)):
        mismatches=[key for key in probe_info if (photons[left][key]['sequence']!=photons[right][key]['sequence'] or photons[left][key]['terminal']!=photons[right][key]['terminal'])]
        passed=candidates[left]['passed'] and candidates[right]['passed'] and not mismatches
        stability.append(dict(scales=[left,right],passed=passed,sequence_or_terminal_mismatches=mismatches))
        if passed and selected is None:selected=left
    if selected is None:failures.append('No stable passing adjacent scale pair; do not broaden the trigger or scale search')
    return dict(passed=not failures,audit_implementation_sha256=digest(Path(__file__)),
        residual_target_world_or_steel_loss_checked=True,selected_scale=selected if not failures else None,
        status='eligible-for-full-engineering-acceptance' if not failures else 'promotion-failed-stop',
        failures=failures,legacy_neutron_reproduced=reproduced,captured_ray_reproduced=optical_reproduced,
        candidates=candidates,scale_stability=stability,jobs=report_jobs,
        scientific_equivalence_claimed=False,final_gap_selected=False,full_acceptance_executed=False)
