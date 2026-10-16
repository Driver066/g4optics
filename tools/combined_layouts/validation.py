"""Independent layout-study validation; no assumptions imported from old matrices."""
from __future__ import annotations
import csv
import re
from pathlib import Path
import numpy as np
import uproot
from study import *

EMPTY_TREES = ('stack_layers','stack_transfers','layout_layers','layout_sensors','layout_transfers','decay_betas')
COUNT_FIELDS = ('generated_optical_photons','scintillation_photons','cerenkov_photons','sipm_detected_photons',
                *(f'sipm_sensor_{i}_detected_photons' for i in range(4)))
EDEP_COMPONENTS = ('electron_tile_edep_mev','proton_tile_edep_mev','other_charged_tile_edep_mev','neutral_tile_edep_mev')


def equal(actual,expected,name,atol=1e-8):
    require(np.allclose(actual,expected,rtol=1e-9,atol=atol,equal_nan=False),'mismatch: '+name)


def counts(table,fields,name):
    for key in fields:
        require(key in table,name+': missing '+key)
        x=table[key]
        require(np.isfinite(x).all() and (x>=0).all() and np.equal(x,np.floor(x)).all(),name+': invalid '+key)


def arrays(tree):
    a=tree.arrays(library='np')
    return {key:a[key] for key in (a.dtype.names if isinstance(a,np.ndarray) else a)}


def log_failures(text):
    pattern=re.compile(r'COMMAND NOT FOUND|COMMAND REFUSED|command fail(?:ed|ure)|\*{5}\s*Illegal|'
       r'illegal application state|illegal parameter|parameter (?:is )?out of range|FatalException|fatal error|'
       r'Run must be terminated|Batch is interrupted|Segmentation fault|\bAborted\b|Overlap is detected',re.I)
    return [line.strip() for line in text.splitlines() if pattern.search(line)]


def audit_readout(tables,config,events):
    r=tables['readout_events'];s=tables['scan'];n=config['sensor_count']
    require(np.array_equal(r['event_id'],np.arange(events)),'readout event coverage')
    require(all(len(v)==events for v in r.values()),'readout column lengths')
    fields=[f'sensor{i}_{kind}' for i in range(4) for kind in ('entries','reflections','step_too_small')]
    counts(r,fields+['no_rindex_steps'],'readout')
    require((r['no_rindex_steps']==0).all(),'all-boundary NoRINDEX')
    deltas=[]
    for i in range(4):
        observed=r[f'sensor{i}_entries'];detected=s[f'sipm_sensor_{i}_detected_photons']
        if i>=n:
            require(all((r[f'sensor{i}_{kind}']==0).all() for kind in ('entries','reflections','step_too_small')),'inactive readout sensor')
            require((detected==0).all(),'inactive scan sensor')
        if config['readout_mode']=='boundary-entry':
            equal(detected,observed,f'corrected sensor {i} entry')
        else:
            require((detected>=observed).all(),'legacy tally below observed true entries')
        deltas.append(int((detected-observed).sum()))
    return {'true_entries':int(sum(r[f'sensor{i}_entries'].sum() for i in range(n))),
            'sensor_true_entries':[int(r[f'sensor{i}_entries'].sum()) for i in range(n)],
            'legacy_excess_over_true_entries':sum(deltas),'sensor_legacy_excess':deltas[:n],
            'sensor_reflections':[int(r[f'sensor{i}_reflections'].sum()) for i in range(n)],
            'sensor_step_too_small':[int(r[f'sensor{i}_step_too_small'].sum()) for i in range(n)],
            'no_rindex_steps':0,
            'caveat':'legacy excess is within the legacy transport history, not the corrected-mode counterfactual; legacy false kills alter subsequent paths'}


def audit_tables(tables,config,events):
    s=tables['scan'];n=config['sensor_count']
    require(np.array_equal(s['event_id'],np.arange(events)),'scan event coverage')
    require(all(len(v)==events for v in s.values()),'scan column lengths')
    counts(s,COUNT_FIELDS,'scan')
    e=s['primary_kinetic_energy_mev']
    require(np.isfinite(e).all() and (e>=0).all() and (e<=2.28).all(),'source energy support')
    require(events<=1 or np.unique(e).size>1,'unexpected monoenergetic beta source')
    equal(s['shoot_x_mm'],0,'source x');equal(s['shoot_y_mm'],0,'source y');equal(s['shoot_z_mm'],config['source_z_mm'],'source z')
    require(np.isin(s['hit_valid'],[0,1]).all(),'hit validity');entered=s['hit_valid'].astype(bool)
    equal(s['hit_z_mm'][entered],config['envelope_thickness_mm']/2,'entrance face',1e-7)
    g,d=s['generated_optical_photons'],s['sipm_detected_photons']
    require(np.array_equal(g,s['scintillation_photons']+s['cerenkov_photons']),'generation component sum')
    require((s['cerenkov_photons']==0).all(),'Cerenkov active')
    require(np.array_equal(d,sum(s[f'sipm_sensor_{i}_detected_photons'] for i in range(n))),'active sensor sum')
    for i in range(n,4):require((s[f'sipm_sensor_{i}_detected_photons']==0).all(),'inactive sensor counts')
    valid=g>0;require(np.array_equal(s['collection_efficiency_valid'],valid),'efficiency validity')
    equal(s['collection_efficiency'][valid],d[valid]/g[valid],'legacy D/G diagnostic')
    require(np.isnan(s['collection_efficiency'][~valid]).all(),'invalid efficiency must be NaN')
    for key in ('tile_edep_mev',*EDEP_COMPONENTS):
        require(np.isfinite(s[key]).all() and (s[key]>=0).all(),'invalid energy '+key)
    equal(s['tile_edep_mev'],sum(s[k] for k in EDEP_COMPONENTS),'edep components');equal(s['steel_edep_mev'],0,'steel disabled')
    result={'events':events,'primary_entered':int(entered.sum()),'generated':int(g.sum()),'detected':int(d.sum()),
            'D_per_source_event':float(d.mean()),'zero_D_events':int((d==0).sum()),'zero_G_events':int((g==0).sum()),
            'sensor_D':[int(s[f'sipm_sensor_{i}_detected_photons'].sum()) for i in range(n)],
            'legacy_D_over_G':float(d.sum()/g.sum()) if g.sum() else None,
            'readout':audit_readout(tables,config,events)}
    c,b=tables['combined_events'],tables['combined_boundaries']
    if not config['combined']:
        require(len(c['event_id'])==len(b['event_id'])==0,'monolith has combined rows')
        return result
    require(np.array_equal(c['event_id'],np.arange(events)),'combined event coverage')
    require(all(len(v)==events for v in c.values()),'combined column lengths')
    transfer=[f'lineage_{p}_to_sensor{i}' for p in ('front','back') for i in range(4)]
    unknown=[f'unknown_to_sensor{i}' for i in range(4)]
    births=[f'all_births_{p}' for p in ('front','back','joint','other')]
    counts(c,['front_generated','back_generated',*transfer,*unknown,*births,'optical_parent_births','boundary_steps','joint_boundary_steps','no_rindex_steps'],'combined')
    require(np.array_equal(c['front_generated']+c['back_generated'],g),'piece generation sums')
    for i in range(4):
        require(np.array_equal(sum(c[f'lineage_{p}_to_sensor{i}'] for p in ('front','back'))+c[f'unknown_to_sensor{i}'],s[f'sipm_sensor_{i}_detected_photons']),'lineage sensor sum')
        require((c[f'unknown_to_sensor{i}']==0).all(),'unknown photon lineage')
    for p in ('front','back','joint'):
        total,optical=c[p+'_edep_mev'],c[p+'_optical_edep_mev']
        require(np.isfinite(total).all() and np.isfinite(optical).all() and (total>=0).all() and (optical>=0).all() and (optical<=total+1e-10).all(),'combined energy validity')
    equal(c['front_edep_mev']+c['back_edep_mev'],s['tile_edep_mev'],'combined tile edep')
    require((c['no_rindex_steps']==0).all(),'combined NoRINDEX')
    counts(b,['event_id','pre_region','post_region','status','count'],'boundaries')
    require(all(len(v)==len(b['event_id']) for v in b.values()),'boundary column lengths')
    require((b['event_id']<events).all() and np.isin(b['pre_region'],list(REGIONS.values())).all() and np.isin(b['post_region'],list(REGIONS.values())).all(),'boundary region/event')
    require((b['count']>0).all(),'sparse zero boundary count')
    keys=list(zip(*(b[k] for k in ('event_id','pre_region','post_region','status'))))
    require(len(keys)==len(set(keys)),'duplicate boundary key')
    for mask,key in [(np.ones(len(b['event_id']),dtype=bool),'boundary_steps'),((b['pre_region']==2)|(b['post_region']==2),'joint_boundary_steps')]:
        equal(np.bincount(b['event_id'][mask].astype(int),weights=b['count'][mask],minlength=events),c[key],'boundary totals '+key)
    require(not (b['status']==14).any(),'boundary NoRINDEX')
    success=np.isin(b['status'],[1,2,12]) & ~np.isin(b['pre_region'],SENSOR_REGIONS)
    for i,region in enumerate(SENSOR_REGIONS):
        mask=success&(b['post_region']==region)
        entries=np.bincount(b['event_id'][mask].astype(int),weights=b['count'][mask],minlength=events)
        equal(entries,tables['readout_events'][f'sensor{i}_entries'],f'boundary/observer sensor {i}')
        if i>=n:
            require(not ((b['pre_region']==region)|(b['post_region']==region)).any(),'inactive sensor in boundary table')
    result['combined']={'births':{k:int(c[k].sum()) for k in (*births,'optical_parent_births')},
          'piece_legacy_G':{p:int(c[p+'_generated'].sum()) for p in ('front','back')},
          'edep':{p:{'total_MeV':float(c[p+'_edep_mev'].sum()),'optical_MeV':float(c[p+'_optical_edep_mev'].sum()),
                     'nonoptical_MeV':float((c[p+'_edep_mev']-c[p+'_optical_edep_mev']).sum())} for p in ('front','back','joint')},
          'joint_boundary_steps':int(c['joint_boundary_steps'].sum()),'no_rindex_steps':0,
          'caveat':'birthplace differs from lineage; candidate post-region and StepTooSmall are not physical transfer; no terminal closure asserted'}
    return result


def audit_summary(path,s,events):
    with Path(path).open() as handle:rows=list(csv.DictReader(handle))
    require(len(rows)==1,'summary rows');r=rows[0]
    require(int(r['events'])==int(r['committed_events'])==events,'summary events')
    for k in ('generated_optical_photons','scintillation_photons','cerenkov_photons','sipm_detected_photons'):
        require(int(r[k])==int(s[k].sum()),'summary count '+k)
    equal(float(r['tile_edep_sum_mev']),s['tile_edep_mev'].sum(),'summary edep')
    d,g=s['sipm_detected_photons'].sum(),s['generated_optical_photons'].sum()
    require(int(r['collection_efficiency_valid'])==int(g>0),'summary efficiency validity')
    if g:equal(float(r['collection_efficiency']),d/g,'summary D/G')
    else:require(np.isnan(float(r['collection_efficiency'])),'summary undefined D/G')
    equal(float(r['net_sipm_photons_per_event']),d/events,'summary D/source')


def _number(line,key):
    match=re.search(r'(?:^|[, ])'+re.escape(key)+r'=([-+0-9.eE]+)',line)
    require(match is not None,'missing runtime '+key);return float(match.group(1))


def _vector(line,key):
    match=re.search(re.escape(key)+r'=\(([^)]+)\)',line)
    require(match is not None,'missing runtime vector '+key)
    return [float(v) for v in match.group(1).split(',')]


def audit_log(text,config):
    require(not log_failures(text),'fatal or failed UI command in log')
    codes=re.findall(r'\*\*\* G4Exception : ([^\s]+)',text)
    require(set(codes)<={'Analysis_W001'},'unexpected exception '+str(codes))
    if codes:require('Merging ntuples is not applicable in sequential application.' in text,'unrecognized analysis warning')
    require('Geometry overlaps check completed !' in text,'missing overlap completion')
    n=config['sensor_count'];placements=[line for line in text.splitlines() if line.startswith('SiPM placement:')]
    require(len(placements)==n,'runtime sensor count')
    for i,(center,local) in enumerate(zip(config['sensor_centers_mm'],config['local_centers'])):
        rows=[line for line in placements if _number(line,'copy')==i];require(len(rows)==1,'runtime sensor copy')
        line=rows[0]
        require('layout='+config['detector_layout']+',' in line and 'face='+config['face']+',' in line,'runtime layout/face')
        equal(_vector(line,'world'),center,'runtime sensor center');equal(_vector(line,'local'),[*local,0],'runtime local center')
        require(f'Checking overlaps for volume SiPM:{i} (G4Box) ... OK!' in text,'missing sensor overlap')
    volumes=[('Tank',0)] if not config['combined'] else [('TankFront',0),('TankBack',1),('CombinedGrease',0)]
    for name,copy in volumes:require(f'Checking overlaps for volume {name}:{copy} (G4Box) ... OK!' in text,'missing tile/joint overlap')
    study_lines=[line for line in text.splitlines() if line.startswith('Standalone layout study:')]
    require(len(study_lines)==1,'missing unique standalone runtime contract');line=study_lines[0]
    require('layout='+config['detector_layout']+',' in line and 'readout='+config['readout_mode'] in line,'standalone layout/readout mismatch')
    require('combined='+str(config['combined']).lower()+',' in line,'standalone combined flag')
    equal(_number(line,'active_sensors'),n,'active sensors');equal(_number(line,'active_thickness'),config['active_thickness_mm'],'active thickness')
    equal(_number(line,'envelope_thickness'),config['envelope_thickness_mm'],'envelope thickness')
    equal(_number(line,'assembly_z_min'),-config['envelope_thickness_mm']/2,'assembly min');equal(_number(line,'assembly_z_max'),config['envelope_thickness_mm']/2,'assembly max')
    bounds=[line for line in text.splitlines() if line.startswith('Standalone volume bounds:')]
    expected_boxes=[]
    if config['combined']:
        for name,copy,center,thickness in [('TankFront',0,config['front_center_z_mm'],config['front_mm']),('TankBack',1,config['back_center_z_mm'],config['back_mm']),('CombinedGrease',0,config['joint_center_z_mm'],config['gap_mm'])]:
            expected_boxes.append((name,copy,[-50,-50,center-thickness/2],[50,50,center+thickness/2]))
    else:expected_boxes.append(('Tank',0,[-50,-50,-config['envelope_thickness_mm']/2],[50,50,config['envelope_thickness_mm']/2]))
    half_sensor=np.array([.25,1.2,1.2] if config['face']=='+X' else [1.2,1.2,.25])
    for i,center in enumerate(config['sensor_centers_mm']):expected_boxes.append(('SiPM',i,np.array(center)-half_sensor,np.array(center)+half_sensor))
    require(len(bounds)==len(expected_boxes),'runtime volume bounds coverage')
    for name,copy,low,high in expected_boxes:
        lines=[line for line in bounds if 'name='+name+',' in line and _number(line,'copy')==copy]
        require(len(lines)==1,'runtime volume bound identity')
        equal(_vector(lines[0],'min'),low,'actual volume min '+name);equal(_vector(lines[0],'max'),high,'actual volume max '+name)
    require((f'Standalone layout geometry: PASS, volumes={len(expected_boxes)}, tiles={2 if config["combined"] else 1}, '
             f'joints={1 if config["combined"] else 0}, sensors={n}, no_positive_volume_overlap=true, outer_contact=true') in text,
            'deterministic standalone geometry check missing')
    contacts=[line for line in text.splitlines() if line.startswith('Standalone sensor contact:')]
    require(len(contacts)==n,'sensor contact coverage')
    for i,center in enumerate(config['sensor_centers_mm']):
        line=next((line for line in contacts if _number(line,'copy')==i),None);require(line is not None,'missing contact copy')
        equal(_vector(line,'center'),center,'contact center');equal(_vector(line,'full_size'),[.5,2.4,2.4] if config['face']=='+X' else [2.4,2.4,.5],'contact sensor size')
        expected={'mono_area':0.,'front_area':0.,'joint_area':0.,'back_area':0.}
        if not config['combined']:expected['mono_area']=5.76
        elif config['face']=='-Z':expected['back_area']=5.76
        else:
            joint=config['joint_center_z_mm'];low,high=joint-.05,joint+.05;half=config['envelope_thickness_mm']/2
            area=lambda lo,hi:2.4*max(0,min(1.2,hi)-max(-1.2,lo))
            expected.update(front_area=area(high,half),joint_area=area(low,high),back_area=area(-half,low))
        for key,value in expected.items():equal(_number(line,key),value,'contact '+key)
        equal(_number(line,'total_area'),5.76,'contact total area');equal(_number(line,'coupling_gap'),0,'contact gap')
    return codes


def validate_inputs(campaign,repo):
    campaign,repo=Path(campaign),Path(repo);m=read_json(campaign/'manifest.json')
    require(m['schema_version']==SCHEMA and m['phase'] in PHASES,'input schema/phase')
    require(m['configurations']==configurations(),'configuration matrix differs')
    require(m['modes']==list(MODES) and m['layouts']==LAYOUTS and m['geometries']==list(GEOMETRIES),'layout/mode contract differs')
    require(m['boundary_regions']==REGIONS and m['success_boundary_statuses']==[1,2,12],'boundary schema differs')
    require(set(m['source_files_sha256'])=={str(p.relative_to(repo)) for p in source_paths(repo)},'frozen source inventory incomplete or expanded')
    for rel,h in m['source_files_sha256'].items():require(digest(safe_child(repo,rel))==h,'frozen source changed: '+rel)
    registry=read_json(campaign/'seed_registry.json');require(digest(campaign/'seed_registry.json')==m['seed_registry_sha256'],'old seed registry changed')
    reserved=set(registry['reserved_seed_values']);events,blocks=PHASES[m['phase']]
    expected={(c['id'],b) for c in m['configurations'] for b in range(blocks)}
    require(len(m['tasks'])==len(expected) and {(t['configuration_id'],t['block']) for t in m['tasks']}==expected,'task matrix coverage')
    require(len({t['id'] for t in m['tasks']})==len(expected),'duplicate task id');used=set()
    for t in m['tasks']:
        c=next(c for c in m['configurations'] if c['id']==t['configuration_id']);index=m['configurations'].index(c)
        require(t['id']==f'{c["id"]}/block-{t["block"]:02d}','task identity')
        require((t['mode'],t['layout'],t['geometry'])==(c['readout_mode'],c['layout'],c['geometry']),'task axes')
        require(t['events']==events and t['seeds']==seed_pair(index,m['phase'],t['block']) and t['geometry_seeds']==seed_pair(index,m['phase'],t['block'],True),'event/seed budget changed')
        values=set(t['seeds']+t['geometry_seeds']);require(not values&(used|reserved),'seed collision');used.update(values)
        path=safe_child(campaign,t['id'])/'run.mac';require(digest(path)==t['macro_sha256'],'macro hash mismatch')
        require(path.read_text()==make_macro(repo,c,events,t['seeds'],t['geometry_seeds']),'macro scientific contract differs')
    require(m['execution']['total_events']==40*blocks*events,'total event budget')
    return m


def audit(campaign,repo,executable=None):
    campaign=Path(campaign);m=validate_inputs(campaign,repo);records=[];binaries=set()
    execution=read_json(campaign/'execution.json')
    require(execution['manifest_sha256']==digest(campaign/'manifest.json'),'execution manifest mismatch')
    for t in m['tasks']:
        c=next(c for c in m['configurations'] if c['id']==t['configuration_id']);folder=campaign/t['id'];r=read_json(folder/'receipt.json')
        require(r['status']=='passed' and r['exit_code']==0 and not r['ui_failure'] and not r['missing_or_empty_outputs'],'failed process receipt')
        require(r['mode']==c['readout_mode'] and r['configuration_id']==c['id'],'receipt configuration')
        for key,path in [('manifest_sha256',campaign/'manifest.json'),('execution_sha256',campaign/'execution.json'),('macro_sha256',folder/'run.mac'),('log_sha256',folder/'simulation.log')]:
            require(r[key]==digest(path),'receipt '+key)
        require(r['macro_sha256']==t['macro_sha256'] and r['events_requested']==t['events'] and r['seeds']==t['seeds'],'receipt input plan')
        require(r['executable_sha256']==execution['executable_sha256'],'execution executable mismatch');binaries.add(r['executable_sha256'])
        row={'task_id':t['id'],'configuration_id':c['id'],'mode':c['readout_mode'],'layout':c['layout'],'geometry':c['geometry'],'block':t['block'],
             'warnings':audit_log((folder/'simulation.log').read_text(),c)}
        names=['run.mac','simulation.log','receipt.json']
        if t['events']:
            with uproot.open(folder/'result.root') as root:
                tables={name:arrays(root[name]) for name in ('scan','combined_events','combined_boundaries','readout_events')}
                require(all(root[name].num_entries==0 for name in EMPTY_TREES),'unrelated tables not empty')
            row.update(audit_tables(tables,c,t['events']));audit_summary(folder/'result_summary.csv',tables['scan'],t['events'])
            names+=['result.root','result_summary.csv'];rng=sorted(folder.glob('*.rndm'));require(bool(rng),'no RNG states');names+=[p.name for p in rng]
        else:row['events']=0
        row['files_sha256']={name:digest(folder/name) for name in names};records.append(row)
    require(len(binaries)==1,'mixed executables')
    if executable:require(binaries=={digest(executable)},'executable changed')
    return {'schema_version':SCHEMA,'status':'passed','phase':m['phase'],'modes':list(MODES),
            'manifest_sha256':digest(campaign/'manifest.json'),'execution_sha256':digest(campaign/'execution.json'),
            'executable_sha256':next(iter(binaries)),'source_files_sha256':m['source_files_sha256'],
            'configurations':m['configurations'],'events':sum(r['events'] for r in records),'tasks':records,
            'scope':'Two explicitly distinct readout definitions; legacy excess is not a corrected-mode counterfactual; no complete terminal closure'}
