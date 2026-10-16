#!/usr/bin/env python3
"""Compare both readout definitions across the fixed 8000-beta matrix."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
import numpy as np
import uproot
from study import *
from validation import arrays


def joint_bootstrap(blocks,config_ids,replicates=10000,seed=12102026):
    require(replicates>=10000,'at least 10000 bootstrap replicates')
    require(set(blocks)==set(config_ids),'bootstrap configuration coverage')
    rng=np.random.default_rng(seed);means=np.zeros((replicates,len(config_ids)))
    for column,key in enumerate(config_ids):
        group=blocks[key];require(group and all(len(x)>0 for x in group),'empty bootstrap block')
        require(all(np.isfinite(x).all() and (x>=0).all() for x in group),'invalid response observations')
        total=sum(len(x) for x in group)
        for block in group:
            for start in range(0,replicates,1000):
                stop=min(start+1000,replicates)
                indexes=rng.integers(0,len(block),size=(stop-start,len(block)))
                means[start:stop,column]+=np.asarray(block)[indexes].sum(axis=1)/total
    return means


def interval(values):
    return np.quantile(values,[.025,.975]).tolist() if np.isfinite(values).all() else None


def _relative(target,reference):
    result=np.full_like(target,np.nan,dtype=float)
    np.divide(target,reference,out=result,where=reference!=0)
    return result


def summarize_contrasts(points,replicates,config_ids):
    positions={key:i for i,key in enumerate(config_ids)};comparisons=[];interactions=[]
    def contrast(kind,target,reference,mode,layout):
        a,b=positions[target],positions[reference];ratios=_relative(replicates[:,a],replicates[:,b])
        ratio=points[a]/points[b] if points[b] else None
        comparisons.append({'kind':kind,'mode':mode,'layout':layout,'target':target,'reference':reference,
          'target_D_per_source':float(points[a]),'reference_D_per_source':float(points[b]),
          'difference_D_per_source':float(points[a]-points[b]),'difference_ci95':interval(replicates[:,a]-replicates[:,b]),
          'ratio':float(ratio) if ratio is not None else None,'ratio_ci95':interval(ratios),
          'percent_change':float(100*(ratio-1)) if ratio is not None else None,'percent_change_ci95':interval(100*(ratios-1)),
          'undefined_ratio_bootstrap_replicates':int((~np.isfinite(ratios)).sum())})
    for mode in MODES:
        for layout in LAYOUTS:
            for joined,mono in JOIN_PAIRS:
                contrast('joining',f'{mode}/{layout}/{joined}',f'{mode}/{layout}/{mono}',mode,layout)
            contrast('orientation',f'{mode}/{layout}/joined16-8',f'{mode}/{layout}/joined8-16',mode,layout)
    for layout in LAYOUTS:
        for geometry in GEOMETRIES:
            name=geometry['id'];contrast('readout-definition',f'boundary-entry/{layout}/{name}',f'legacy/{layout}/{name}','boundary-entry vs legacy',layout)
    for mode in MODES:
        for layout in LAYOUTS:
            if layout=='side-two':continue
            for joined,mono in JOIN_PAIRS:
                keys=[f'{mode}/{layout}/{joined}',f'{mode}/{layout}/{mono}',f'{mode}/side-two/{joined}',f'{mode}/side-two/{mono}']
                a,b,c,d=[positions[k] for k in keys]
                ratio_layout=_relative(replicates[:,a],replicates[:,b]);ratio_side=_relative(replicates[:,c],replicates[:,d])
                q=_relative(ratio_layout,ratio_side)
                point=(points[a]/points[b])/(points[c]/points[d]) if points[b] and points[c] and points[d] else None
                interactions.append({'mode':mode,'layout':layout,'reference_layout':'side-two','joined_geometry':joined,'mono_geometry':mono,
                     'Q_ratio_of_joining_ratios':float(point) if point is not None else None,'Q_ci95':interval(q),
                     'undefined_bootstrap_replicates':int((~np.isfinite(q)).sum()),
                     'interpretation':'Q=1 means equal relative joining response; pointwise interval does not establish equivalence'})
    return comparisons,interactions


def write_csv(path,rows):
    require(bool(rows),'empty CSV data')
    with Path(path).open('x',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def flatten_interval_rows(rows):
    output=[]
    for row in rows:
        flat={}
        for key,value in row.items():
            if key.endswith('ci95'):
                flat[key+'_low']=value[0] if value is not None else None
                flat[key+'_high']=value[1] if value is not None else None
            else:flat[key]=value
        output.append(flat)
    return output


def analyze(campaign,report,output):
    campaign,report,output=Path(campaign),Path(report),Path(output)
    require(not output.exists(),'analysis output already exists')
    m,a=read_json(campaign/'manifest.json'),read_json(report)
    require(m['schema_version']==SCHEMA and m['phase']=='explore','wrong analysis campaign')
    require(m['configurations']==configurations() and m['execution']['total_events']==8000 and len(m['tasks'])==80,'not fixed 8000-beta matrix')
    require(a['status']=='passed' and a['phase']=='explore' and a['manifest_sha256']==digest(campaign/'manifest.json'),'audit stale or not passed')
    records={r['task_id']:r for r in a['tasks']};require(len(records)==80 and set(records)=={t['id'] for t in m['tasks']},'audit task matrix')
    ids=[c['id'] for c in m['configurations']];blocks={key:[] for key in ids};event_rows=[];readout_rows=[];combined_rows=[];boundary_rows=[]
    for t in sorted(m['tasks'],key=lambda t:(ids.index(t['configuration_id']),t['block'])):
        require(t['events']==100 and t['block'] in (0,1),'changed analysis event budget')
        f=campaign/t['id'];r=records[t['id']]
        for name,h in r['files_sha256'].items():require(digest(safe_child(f,name))==h,'audited output changed: '+t['id']+'/'+name)
        with uproot.open(f/'result.root') as root:
            s,c,b,o=[arrays(root[name]) for name in ('scan','combined_events','combined_boundaries','readout_events')]
        require(np.array_equal(s['event_id'],np.arange(100)),'analysis event coverage')
        blocks[t['configuration_id']].append(s['sipm_detected_photons'])
        axes={'mode':t['mode'],'layout':t['layout'],'geometry':t['geometry'],'block':t['block']}
        for i in range(100):
            row={**axes,'event_id':i}
            for key in ('primary_kinetic_energy_mev','shoot_z_mm','hit_valid','generated_optical_photons','scintillation_photons','sipm_detected_photons',
                        *(f'sipm_sensor_{j}_detected_photons' for j in range(4)),'tile_edep_mev'):
                row[key]=s[key][i].item()
            true=sum(int(o[f'sensor{j}_entries'][i]) for j in range(4));row['observed_true_entries']=true
            row['tally_excess_over_observed_entry']=int(s['sipm_detected_photons'][i])-true
            event_rows.append(row)
        for table,target in ((o,readout_rows),(c,combined_rows),(b,boundary_rows)):
            for i in range(len(table['event_id'])):
                row={**axes,**{key:value[i].item() for key,value in table.items()}}
                if table is c:
                    for piece in ('front','back','joint'):row[piece+'_nonoptical_edep_mev']=row[piece+'_edep_mev']-row[piece+'_optical_edep_mev']
                target.append(row)
    require(all(len(x)==2 for x in blocks.values()),'missing seed blocks')
    plan=m['statistical_plan'];require(plan['bootstrap_replicates']==10000 and plan['bootstrap_seed']==12102026,'statistical plan changed')
    matrix=joint_bootstrap(blocks,ids,plan['bootstrap_replicates'],plan['bootstrap_seed'])
    points=np.array([np.concatenate(blocks[key]).mean() for key in ids]);summaries=[]
    for index,config in enumerate(m['configurations']):
        d=np.concatenate(blocks[config['id']]);rows=[r for r in event_rows if (r['mode'],r['layout'],r['geometry'])==(config['readout_mode'],config['layout'],config['geometry'])]
        summaries.append({'configuration_id':config['id'],'mode':config['readout_mode'],'layout':config['layout'],'geometry':config['geometry'],
          'events':len(d),'sensor_count':config['sensor_count'],'sensor_area_mm2':config['sensor_area_mm2'],
          'D_sum':int(d.sum()),'D_per_source_event':float(d.mean()),'D_per_source_ci95':interval(matrix[:,index]),
          'D_per_source_per_mm2':float(d.mean()/config['sensor_area_mm2']),'zero_D_events':int((d==0).sum()),
          'observed_true_entries_per_source':sum(r['observed_true_entries'] for r in rows)/len(rows),
          'legacy_excess_per_source':sum(r['tally_excess_over_observed_entry'] for r in rows)/len(rows),
          'G_legacy_per_source':sum(r['generated_optical_photons'] for r in rows)/len(rows),
          'primary_energy_mean_MeV':sum(r['primary_kinetic_energy_mev'] for r in rows)/len(rows),
          'hit_events':sum(r['hit_valid'] for r in rows),'block0_D_mean':float(blocks[config['id']][0].mean()),'block1_D_mean':float(blocks[config['id']][1].mean())})
    comparisons,interactions=summarize_contrasts(points,matrix,ids)
    result={'schema_version':SCHEMA,'scope':'40 configurations, 8000 source beta events; both readout definitions reported separately',
      'manifest_sha256':digest(campaign/'manifest.json'),'audit_sha256':digest(report),
      'bootstrap':{'replicates':10000,'seed':12102026,'configurations':ids,
        'method':'whole-event resampling independently within each seed block; joint mean matrix reused for all shared controls',
        'intervals':'pointwise descriptive percentile 95%; not multiplicity adjusted; no equivalence test'},
      'readout_definitions':{'legacy':'pre-step SiPM tally; can count and kill reflected recovery tracks',
         'boundary-entry':'corrected physical SiPM entry tally, no PDE'},
      'caveats':['Legacy passive true entries are observed under legacy false-kill transport, not a corrected-mode counterfactual.',
        'Whole readout histories are independent; equal seed numbers would not make legacy and corrected events paired.',
        'Different sensor counts/areas mean cross-layout absolute D is not an isolated placement causal effect; area normalization does not remove this issue.',
        'Source misses and zero responses are included; G and tile edep retain legacy meanings.',
        'Counts of candidate boundaries include reflections and StepTooSmall; they are not unique transmissions or terminal closure.',
        'Fixed 100x100 mm, bare polished, point source, effective EJ-550 attenuation; no laboratory calibration claim.'],
      'summaries':summaries,'comparisons':comparisons,'interactions':interactions}
    output.mkdir(parents=True)
    np.savez_compressed(output/'bootstrap_means.npz',configuration_ids=np.array(ids),means=matrix)
    result['bootstrap']['matrix_sha256']=digest(output/'bootstrap_means.npz')
    for filename,rows in [('events.csv',event_rows),('readout_events.csv',readout_rows),('combined_events.csv',combined_rows),('combined_boundaries.csv',boundary_rows),
                          ('summary.csv',flatten_interval_rows(summaries)),('comparisons.csv',flatten_interval_rows(comparisons)),('interactions.csv',flatten_interval_rows(interactions))]:
        write_csv(output/filename,rows)
    write_json(output/'analysis.json',result)
    lines=['# Four-layout combined-tile comparison','','Both readout definitions are retained; legacy is not a true-entry measurement. All 8000 source events, including misses and zeros, are included.','',
           '| Readout | Layout | Geometry | D/source [pointwise 95% CI] | True entries/source | Legacy excess/source |','|---|---|---|---:|---:|---:|']
    for r in summaries:
        lo,hi=r['D_per_source_ci95'];lines.append(f"| {r['mode']} | {r['layout']} | {r['geometry']} | {r['D_per_source_event']:.4g} [{lo:.4g}, {hi:.4g}] | {r['observed_true_entries_per_source']:.4g} | {r['legacy_excess_per_source']:.4g} |")
    lines+=['','Joining comparisons are within the same layout and mode. Orientation, readout-definition and layout×joining Q contrasts are exported separately; shared mono24 controls use the same resampled mean in each bootstrap replicate.','',*[f'- {x}' for x in result['caveats']], '']
    (output/'report.md').write_text('\n'.join(lines));return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',required=True,type=Path);p.add_argument('--audit',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();r=analyze(a.campaign,a.audit,a.output)
    print(f"Analyzed 8000 events; {len(r['comparisons'])} direct contrasts and {len(r['interactions'])} interactions: {a.output}")

if __name__=='__main__':main()
