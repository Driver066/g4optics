#!/usr/bin/env python3
"""Prepare the fixed 40-configuration two-readout study, without simulation."""
from pathlib import Path
import argparse
import subprocess
from study import *


def prepare(repo, output, phase):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    require(phase in PHASES, 'invalid phase')
    require(not output.exists(), 'refusing existing output directory')
    events, blocks = PHASES[phase]; configs = configurations(); registry = old_seed_registry(repo)
    reserved = set(registry['reserved_seed_values']); prepared = []; used = set()
    for index, config in enumerate(configs):
        for block in range(blocks):
            seeds = seed_pair(index,phase,block); geometry_seeds = seed_pair(index,phase,block,True)
            values = set(seeds+geometry_seeds)
            require(not values & reserved, 'new seed overlaps old registered study')
            require(not values & used, 'duplicate seed across new tasks'); used.update(values)
            prepared.append((config,block,seeds,geometry_seeds,make_macro(repo,config,events,seeds,geometry_seeds)))
    files = source_paths(repo)
    m = {'schema_version':SCHEMA,'phase':phase,'base_commit':BASE_COMMIT,
         'working_head':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
         'source_root_at_preparation':str(repo),'source_files_sha256':{str(p.relative_to(repo)):digest(p) for p in files},
         'modes':list(MODES),'layouts':LAYOUTS,'geometries':list(GEOMETRIES),'configurations':configs,
         'boundary_regions':REGIONS,'success_boundary_statuses':[1,2,12],
         'conditions':{'size_xy_mm':[100,100],'sensor_size_mm':[2.4,2.4,.5],'surface':'bare polished',
              'source':'original sr90_allowed_beta_v1; central -Z point source 1.5 mm above entrance envelope',
              'back_mount':'one detector group on final assembly -Z exterior; no detectors at internal interfaces',
              'grease':'EJ-550; n=1.46; 0.1 mm; effective L=-0.1/ln(T) from digitized 280-500 nm transmission',
              'grease_abslength_vector':absorption_vector(repo/TRANSMISSION),
              'material_caveat':'effective sample attenuation does not separate bulk, reflection, scattering losses; actual scintillation energy support is inside measured wavelengths',
              'legacy_semantics':'historical pre-step SiPM tally, known reflected-recovery false detections; not true photon entry',
              'boundary_entry_semantics':'physical transmission/refraction/same-material entry into ideal sensor; no PDE',
              'generated_semantics':'legacy non-optical-parent scintillation plus Cerenkov; not full optical births',
              'energy_semantics':'legacy tile edep includes optical deposition; combined diagnostics split optical/nonoptical'},
         'execution':{'events_per_task':events,'blocks_per_configuration':blocks,'total_events':40*blocks*events,
              'tasks':40*blocks,'run_manager':'Serial','one_process_per_task':True,'automatic_retry':False,
              'seed_policy':'every mode/layout/geometry/block/phase has distinct 20261012-series source and geometry seeds; no paired-event assumption'},
         'statistical_plan':{'primary_outcome':'D per all source beta events including misses and zero responses',
              'bootstrap_replicates':10000,'bootstrap_seed':12102026,
              'method':'independent event resampling within blocks; one joint bootstrap mean matrix reused for every shared-control contrast',
              'interval_scope':'pointwise descriptive 95%, no multiple-comparison adjustment, no equivalence test',
              'source_modes':'both readout modes retained and never pooled','adaptive_extension':False},
         'tasks':[]}
    output.mkdir(parents=True)
    write_json(output/'seed_registry.json',registry);m['seed_registry_sha256']=digest(output/'seed_registry.json')
    for config,block,seeds,geometry_seeds,macro in prepared:
        task_id=f'{config["id"]}/block-{block:02d}';folder=output/task_id;folder.mkdir(parents=True)
        (folder/'run.mac').write_text(macro)
        m['tasks'].append({'id':task_id,'configuration_id':config['id'],'mode':config['readout_mode'],
             'layout':config['layout'],'geometry':config['geometry'],'block':block,'events':events,
             'seeds':seeds,'geometry_seeds':geometry_seeds,'macro_sha256':digest(folder/'run.mac')})
    write_json(output/'manifest.json',m)
    return m


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--phase',choices=tuple(PHASES),required=True)
    args=parser.parse_args();m=prepare(args.repo,args.output,args.phase)
    print(f"Prepared {len(m['tasks'])} independent processes, {m['execution']['total_events']} beta events: {args.output}")


if __name__=='__main__': main()
