#!/usr/bin/env python3
"""Prepare/audit 1100 optical primaries for four layouts and two readouts; no MC launch."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from study import (LAYOUTS, MODES, PHASES, REGIONS, SENSOR_REGIONS, command_lines,
                   configurations, digest, old_seed_registry, read_json, registered_seeds,
                   require, safe_child, seed_pair, write_json)

SCHEMA = 'combined-layouts-probes-v1'
SEED_BASE = 2026200000
EVENTS_PER_TASK = 50
SUCCESS = (1, 2, 12)
SCOPE = ('Controlled optical engineering probes, separate from the 8000 source-beta sample. '
         'Legacy D is the historical pre-step tally. Passive true entries follow that same '
         'legacy trajectory and are not a corrected-mode counterfactual.')


def specifications():
    specs = []
    for config in configurations():
        if config['geometry'] != 'joined8-8':
            continue
        for sensor, center in enumerate(config['sensor_centers_mm']):
            if config['face'] == '+X':
                position = [center[0] - .25 - .01, center[1], .65]
                direction, angle, source_region = [1, 0, 0], 0, REGIONS['front']
            else:
                position = [center[0], center[1], center[2] + .25 + .01]
                direction, angle, source_region = [0, 0, -1], 90, REGIONS['back']
            specs.append({'id': f'{config["id"]}/plastic-s{sensor}', 'configuration_id': config['id'],
                          'kind': 'plastic', 'mode': config['readout_mode'], 'layout': config['layout'],
                          'geometry': config['geometry'], 'sensor': sensor,
                          'sensor_region': SENSOR_REGIONS[sensor], 'source_region': source_region,
                          'position_mm': position, 'direction': direction,
                          'polarization': [0, 1, 0], 'polarization_angle_deg': angle,
                          'particle': 'opticalphoton', 'energy_ev': 3.0, 'events': EVENTS_PER_TASK})
        if config['layout'] == 'side-two':
            for sensor, center in enumerate(config['sensor_centers_mm']):
                specs.append({'id': f'{config["id"]}/joint-seam-s{sensor}', 'configuration_id': config['id'],
                              'kind': 'joint-seam', 'mode': config['readout_mode'], 'layout': config['layout'],
                              'geometry': config['geometry'], 'sensor': sensor,
                              'sensor_region': SENSOR_REGIONS[sensor], 'source_region': REGIONS['joint'],
                              'position_mm': [center[0] - .25 - .01, center[1], 0.0],
                              'direction': [1, 0, 0], 'polarization': [0, 1, 0], 'polarization_angle_deg': 0,
                              'particle': 'opticalphoton', 'energy_ev': 3.0, 'events': EVENTS_PER_TASK})
    for index, probe in enumerate(specs):
        probe['seeds'] = [SEED_BASE + 4 * index + 1, SEED_BASE + 4 * index + 2]
        probe['geometry_seeds'] = [value + 10000 for value in probe['seeds']]
    require(len(specs) == 22 and sum(p['events'] for p in specs) == 1100, 'probe budget changed')
    require(len({value for p in specs for value in p['seeds'] + p['geometry_seeds']}) == 88,
            'probe streams are not distinct')
    return specs


def make_macro(smoke_text, config, probe):
    lines = command_lines(smoke_text)
    for command in ('/opnovice2/combined/enabled true', '/opnovice2/combined/layoutStudy true',
                    '/opnovice2/combined/layers 8 0.1 8 mm',
                    '/opnovice2/sipm/readoutMode ' + probe['mode'],
                    '/opnovice2/sipm/layout ' + config['detector_layout'],
                    '/opnovice2/sipm/face ' + config['face']):
        require(lines.count(command) == 1, 'missing/duplicate required base command: ' + command)
    require(lines.count('/run/initialize') == 1, 'initialize must be unique')
    require(sum(line.startswith('/opnovice2/sipm/readoutMode ') for line in lines) == 1,
            'conflicting readout modes')
    require(not any(line.startswith(('/opnovice2/gun/randomDirection', '/control/execute')) for line in lines),
            'unexpected random-direction or nested macro command')
    kept = [line for line in lines if not (
        line.split()[0].startswith(('/gps/', '/random/', '/geometry/test/'))
        or line.split()[0] in ('/run/beamOn', '/run/printProgress',
                               '/opnovice2/gun/electronEnergyMode', '/opnovice2/gun/optPhotonPolar'))]
    vec = lambda values: ' '.join(f'{value:.17g}' for value in values)
    kept += ['/gps/particle opticalphoton', '/gps/number 1', '/gps/pos/type Point',
             '/gps/pos/centre ' + vec(probe['position_mm']) + ' mm',
             '/gps/direction ' + vec(probe['direction']), '/gps/polarization 0 1 0',
             '/gps/ene/type Mono', '/gps/ene/mono 3 eV',
             f'/opnovice2/gun/optPhotonPolar {probe["polarization_angle_deg"]} deg',
             '/random/setSeeds ' + vec(probe['geometry_seeds']),
             '/geometry/test/resolution 10000', '/geometry/test/run',
             '/random/setSeeds ' + vec(probe['seeds']),
             '/random/setSavingFlag true', '/random/setDirectoryName ./',
             '/run/printProgress 10', f'/run/beamOn {probe["events"]}']
    require(not any(line.startswith(('/gps/hist/', '/gps/energy ', '/gps/clear', '/gps/source/')) for line in kept),
            'unexpected inherited spectrum or GPS source mutation')
    return f'# {SCHEMA}: {probe["id"]}\n# {SCOPE}\n' + '\n'.join(kept) + '\n'


def prepare(smoke, output):
    smoke, output = Path(smoke).resolve(), Path(output).resolve()
    require(not output.exists(), 'refusing existing probe directory')
    source_manifest = read_json(smoke / 'manifest.json')
    require(source_manifest['phase'] == 'smoke', 'probe templates require a smoke campaign')
    repo = Path(source_manifest['source_root_at_preparation'])
    from validation import validate_inputs as validate_study_inputs
    validate_study_inputs(smoke, repo)
    configs = [c for c in configurations() if c['geometry'] == 'joined8-8']
    config_by_id = {c['id']: c for c in configs}
    sources = dict(source_manifest['source_files_sha256'])
    sources['tools/combined_layouts/probes.py'] = digest(Path(__file__))
    registry = old_seed_registry(repo)
    reserved = set(registry['reserved_seed_values']) | registered_seeds(source_manifest)
    for index in range(40):
        for phase, (_, blocks) in PHASES.items():
            for block in range(blocks):
                for geometry in (False, True):
                    reserved.update(seed_pair(index, phase, block, geometry))
    specs = specifications()
    require(not registered_seeds(specs) & reserved, 'probe seeds overlap prior or planned beta streams')
    references = {}
    for index, config in enumerate(configs):
        base_tasks = [t for t in source_manifest['tasks'] if t['configuration_id'] == config['id']]
        require(len(base_tasks) == 1, 'expected one smoke task per configuration')
        task = base_tasks[0]
        macro = safe_child(smoke, task['id']) / 'run.mac'
        require(digest(macro) == task['macro_sha256'], 'smoke macro changed')
        references[config['id']] = {'path': f'reference-smoke/{index:02d}.mac',
                                    'text': macro.read_text(), 'sha256': digest(macro),
                                    'smoke_task_id': task['id']}
    macros = [(probe, make_macro(references[probe['configuration_id']]['text'],
                                 config_by_id[probe['configuration_id']], probe)) for probe in specs]
    m = {'schema_version': SCHEMA, 'phase': 'probe', 'scope': SCOPE,
         'base_commit': source_manifest['base_commit'], 'working_head': source_manifest['working_head'],
         'source_root_at_preparation': str(repo), 'source_files_sha256': sources,
         'probe_tool_sha256': digest(Path(__file__)), 'configurations': configs,
         'modes': list(MODES), 'layouts': LAYOUTS, 'boundary_regions': REGIONS,
         'success_boundary_statuses': list(SUCCESS),
         'base_smoke': {'path': str(smoke), 'manifest_sha256': digest(smoke / 'manifest.json')},
         'conditions': {**source_manifest['conditions'],
                        'source': 'One 3 eV optical photon; case-specific position/direction and fixed +Y polarization.',
                        'probe_geometry': 'joined8-8; plastic launch 10 micrometres inside the chosen receiving face; Side 2 plastic launch z=+0.65 mm; separate glue-seam launch z=0.',
                        'interpretation': SCOPE},
         'execution': {'total_events': 1100, 'optical_primaries': 1100, 'diagnostic_electron_primaries': 0,
                       'events_per_task': EVENTS_PER_TASK, 'tasks': 22, 'one_process_per_task': True,
                       'run_manager': 'Serial', 'automatic_retry': False,
                       'seed_policy': 'Distinct mode/layout/sensor/kind and geometry/source streams; no event pairing across modes.',
                       'seed_base': SEED_BASE},
         'reference_macros': {key: {k: v for k, v in ref.items() if k != 'text'} for key, ref in references.items()},
         'seed_registry': registry, 'reserved_core_phase_seeds_checked': True, 'tasks': []}
    output.mkdir(parents=True)
    (output / 'reference-smoke').mkdir()
    for ref in references.values():
        (output / ref['path']).write_text(ref['text'])
    for probe, macro in macros:
        task_id = probe['id'] + '/block-00'
        folder = output / task_id
        folder.mkdir(parents=True)
        (folder / 'run.mac').write_text(macro)
        m['tasks'].append({'id': task_id, 'configuration_id': probe['configuration_id'],
                           'mode': probe['mode'], 'layout': probe['layout'], 'geometry': probe['geometry'],
                           'block': 0, 'events': probe['events'], 'seeds': probe['seeds'],
                           'geometry_seeds': probe['geometry_seeds'], 'macro_sha256': digest(folder / 'run.mac'),
                           'probe': probe})
    write_json(output / 'manifest.json', m)
    return m


def validate_inputs(campaign, repo):
    campaign, repo = Path(campaign), Path(repo)
    m = read_json(campaign / 'manifest.json')
    require(m['schema_version'] == SCHEMA and m['phase'] == 'probe', 'not a layout probe campaign')
    expected_configs = [c for c in configurations() if c['geometry'] == 'joined8-8']
    require(m['configurations'] == expected_configs, 'probe configurations changed')
    require(m['boundary_regions'] == REGIONS and m['success_boundary_statuses'] == list(SUCCESS),
            'probe boundary contract changed')
    require(m['execution']['total_events'] == 1100 and m['execution']['optical_primaries'] == 1100
            and m['execution']['diagnostic_electron_primaries'] == 0, 'probe budget changed')
    for relative, expected in m['source_files_sha256'].items():
        require(digest(safe_child(repo, relative)) == expected, 'frozen probe source changed: ' + relative)
    require(digest(Path(__file__)) == m['probe_tool_sha256'], 'probe authoring tool changed')
    specs = specifications()
    require(len(m['tasks']) == len(specs), 'probe task count changed')
    by_id = {c['id']: c for c in expected_configs}
    for task, probe in zip(m['tasks'], specs):
        require(task['probe'] == probe, 'probe specification changed')
        require(task['id'] == probe['id'] + '/block-00' and task['configuration_id'] == probe['configuration_id'],
                'probe task identity changed')
        require(task['block'] == 0 and task['events'] == probe['events'] and task['seeds'] == probe['seeds']
                and task['geometry_seeds'] == probe['geometry_seeds'] and task['mode'] == probe['mode']
                and task['layout'] == probe['layout'] and task['geometry'] == probe['geometry'],
                'probe event/seed/mode contract changed')
        ref = m['reference_macros'][probe['configuration_id']]
        reference_path = safe_child(campaign, ref['path'])
        require(digest(reference_path) == ref['sha256'], 'reference smoke macro changed')
        path = safe_child(campaign, task['id']) / 'run.mac'
        require(digest(path) == task['macro_sha256'], 'probe macro hash mismatch')
        require(path.read_text() == make_macro(reference_path.read_text(), by_id[probe['configuration_id']], probe),
                'probe macro no longer matches its source contract')
    return m


def audit_tables(tables, config, probe):
    import numpy as np
    from validation import counts, equal
    a, c, b, r = (tables[name] for name in ('scan', 'combined_events', 'combined_boundaries', 'readout_events'))
    n = probe['events']
    for name, table in (('scan', a), ('combined_events', c), ('readout_events', r)):
        require(np.array_equal(table['event_id'], np.arange(n)), name + ': event coverage')
        require(all(len(value) == n for value in table.values()), name + ': inconsistent column lengths')
    equal(a['primary_kinetic_energy_mev'], 3e-6, '3 eV optical source', atol=1e-14)
    for axis, coordinate in zip('xyz', probe['position_mm']):
        equal(a[f'shoot_{axis}_mm'], coordinate, 'source ' + axis)
    scan_counts = ['generated_optical_photons', 'scintillation_photons', 'cerenkov_photons',
                   'sipm_detected_photons', *[f'sipm_sensor_{i}_detected_photons' for i in range(4)]]
    counts(a, scan_counts, 'scan')
    g, d = a['generated_optical_photons'], a['sipm_detected_photons']
    valid = g > 0
    require(np.array_equal(a['collection_efficiency_valid'], valid), 'legacy efficiency validity')
    equal(a['collection_efficiency'][valid], d[valid] / g[valid], 'legacy efficiency')
    require(np.isnan(a['collection_efficiency'][~valid]).all(), 'zero legacy generation needs NaN efficiency')
    equal(a['sipm_detected_photons'], sum(a[f'sipm_sensor_{i}_detected_photons'] for i in range(4)), 'all sensor sum')
    birth_fields = [f'all_births_{piece}' for piece in ('front', 'back', 'joint', 'other')]
    lineage_fields = [f'lineage_{piece}_to_sensor{i}' for piece in ('front', 'back') for i in range(4)]
    unknown_fields = [f'unknown_to_sensor{i}' for i in range(4)]
    counts(c, ['front_generated', 'back_generated', *birth_fields, *lineage_fields, *unknown_fields,
               'optical_parent_births', 'boundary_steps', 'joint_boundary_steps', 'no_rindex_steps'], 'combined_events')
    require((a['sipm_detected_photons'] <= 1 + sum(c[key] for key in birth_fields)).all(),
            'more tallies than primary plus secondary optical tracks')
    equal(c['front_generated'] + c['back_generated'], a['generated_optical_photons'], 'per-piece legacy generation')
    for piece in ('front', 'back', 'joint'):
        total, optical = c[f'{piece}_edep_mev'], c[f'{piece}_optical_edep_mev']
        require(np.isfinite(total).all() and np.isfinite(optical).all() and (total >= 0).all()
                and (optical >= 0).all() and (optical <= total + 1e-12).all(), 'invalid combined energy')
    equal(c['front_edep_mev'] + c['back_edep_mev'], a['tile_edep_mev'], 'tile energy sum')
    counts(b, ['event_id', 'pre_region', 'post_region', 'status', 'count'], 'boundaries')
    require(all(len(value) == len(b['event_id']) for value in b.values()), 'boundary column lengths')
    require((b['event_id'] < n).all() and (b['pre_region'] <= 9).all()
            and (b['post_region'] <= 9).all() and (b['status'] <= 42).all() and (b['count'] > 0).all(),
            'invalid sparse boundary row')
    keys = list(zip(b['event_id'], b['pre_region'], b['post_region'], b['status']))
    require(len(keys) == len(set(keys)), 'duplicate sparse boundary key')
    joint = (b['pre_region'] == 2) | (b['post_region'] == 2)
    for mask, field in ((np.ones(len(b['event_id']), dtype=bool), 'boundary_steps'), (joint, 'joint_boundary_steps'),
                        (b['status'] == 14, 'no_rindex_steps')):
        equal(np.bincount(b['event_id'][mask].astype(int), weights=b['count'][mask], minlength=n), c[field], field)
    require(not np.any(np.isin(b['status'], [0, 14])) and np.all(c['no_rindex_steps'] == 0), 'Undefined/NoRINDEX boundary')
    counts(r, [*[f'sensor{i}_{suffix}' for i in range(4) for suffix in ('entries', 'reflections', 'step_too_small')],
               'no_rindex_steps'], 'readout_events')
    require((r['no_rindex_steps'] == 0).all(), 'readout NoRINDEX')
    true_totals, tally_totals, excess_totals, reflected_without_entry = [], [], [], []
    for sensor, region in enumerate(SENSOR_REGIONS):
        tallies = a[f'sipm_sensor_{sensor}_detected_photons']
        candidate = (~np.isin(b['pre_region'], SENSOR_REGIONS)) & (b['post_region'] == region)
        successful = candidate & np.isin(b['status'], SUCCESS)
        actual = np.bincount(b['event_id'][successful].astype(int), weights=b['count'][successful], minlength=n)
        equal(actual, r[f'sensor{sensor}_entries'], 'passive true entry sensor ' + str(sensor))
        equal(c[f'lineage_front_to_sensor{sensor}'] + c[f'lineage_back_to_sensor{sensor}']
              + c[f'unknown_to_sensor{sensor}'], tallies, 'lineage tally sensor ' + str(sensor))
        if probe['mode'] == 'boundary-entry':
            require(np.array_equal(tallies, actual), 'corrected D differs from successful boundary entries')
        else:
            require((tallies >= actual).all(), 'legacy tally is below its own trajectory true entries')
        if sensor >= config['sensor_count']:
            equal(tallies, 0, 'inactive sensor tally')
            equal(actual, 0, 'inactive sensor true entry')
        reflected = candidate & np.isin(b['status'], [3, 4])
        refl_events = np.bincount(b['event_id'][reflected].astype(int), weights=b['count'][reflected], minlength=n)
        reflected_without_entry.append(np.flatnonzero((refl_events > 0) & (actual == 0)).tolist())
        true_totals.append(int(actual.sum())); tally_totals.append(int(tallies.sum()))
        excess_totals.append(int((tallies - actual).sum()))
    require(true_totals[probe['sensor']] > 0 and tally_totals[probe['sensor']] > 0,
            'intended active sensor never received a true entry')
    return {'events': n, 'mode': probe['mode'], 'layout': probe['layout'], 'probe_kind': probe['kind'],
            'target_sensor': probe['sensor'], 'sensor_true_entries': true_totals,
            'sensor_D_tallies': tally_totals, 'legacy_tally_excess_over_own_true_entries': excess_totals,
            'reflected_without_later_sensor_entry_event_ids': reflected_without_entry,
            'legacy_generated': int(a['generated_optical_photons'].sum()),
            'optical_secondary_births': int(sum(c[key].sum() for key in birth_fields)),
            'no_rindex_steps': 0,
            'interpretation': SCOPE}


def audit(campaign, repo, executable=None):
    import uproot
    from validation import arrays, audit_log, audit_readout, audit_summary
    campaign, repo = Path(campaign), Path(repo)
    m = validate_inputs(campaign, repo)
    configs = {c['id']: c for c in m['configurations']}
    reports, binaries = [], set()
    for task in m['tasks']:
        folder = safe_child(campaign, task['id'])
        receipt = read_json(folder / 'receipt.json')
        require(receipt['exit_code'] == 0 and receipt.get('status') == 'passed',
                'probe process did not pass execution checks')
        require(receipt.get('events_requested') == task['events'] and receipt.get('seeds') == task['seeds'],
                'probe receipt event/seed plan mismatch')
        require(receipt.get('geometry_seeds') == task['geometry_seeds'], 'probe receipt geometry seeds differ')
        for field in ('configuration_id', 'mode', 'layout', 'geometry'):
            require(receipt.get(field) == task[field], 'probe receipt identity differs: ' + field)
        for field, path in (('macro_sha256', folder / 'run.mac'), ('log_sha256', folder / 'simulation.log'),
                            ('manifest_sha256', campaign / 'manifest.json'),
                            ('execution_sha256', campaign / 'execution.json')):
            require(receipt[field] == digest(path), 'receipt mismatch: ' + field)
        binaries.add(receipt['executable_sha256'])
        config = configs[task['configuration_id']]
        warnings = audit_log((folder / 'simulation.log').read_text(), config)
        with uproot.open(folder / 'result.root') as root:
            tables = {name: arrays(root[name]) for name in ('scan', 'combined_events', 'combined_boundaries', 'readout_events')}
            require(all(root[name].num_entries == 0 for name in ('stack_layers', 'stack_transfers', 'layout_layers',
                                                                 'layout_sensors', 'layout_transfers', 'decay_betas')),
                    'unrelated tables are not empty')
        report = audit_tables(tables, config, task['probe'])
        report['readout'] = audit_readout(tables, config, task['events'])
        audit_summary(folder / 'result_summary.csv', tables['scan'], task['events'])
        rng = sorted(folder.glob('*.rndm'))
        require(bool(rng), 'missing random state evidence')
        paths = [folder / name for name in ('run.mac', 'receipt.json', 'simulation.log', 'result.root', 'result_summary.csv')] + rng
        report.update(task_id=task['id'], configuration_id=config['id'], warnings=warnings,
                      files_sha256={path.name: digest(path) for path in paths})
        reports.append(report)
    require(len(binaries) == 1, 'probe executable identities differ')
    if executable is not None:
        require(binaries == {digest(executable)}, 'probe executable changed')
    return {'schema_version': SCHEMA, 'status': 'passed', 'phase': 'probe', 'scope': SCOPE,
            'manifest_sha256': digest(campaign / 'manifest.json'), 'executable_sha256': next(iter(binaries)),
            'source_files_sha256': m['source_files_sha256'], 'total_optical_primaries': 1100,
            'total_diagnostic_electrons': 0, 'tasks': reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--smoke-campaign', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('audit')
    check.add_argument('--campaign', type=Path, required=True)
    check.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    check.add_argument('--executable', type=Path)
    check.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare(args.smoke_campaign, args.output)
        print(f'Prepared {len(result["tasks"])} probe tasks / 1100 optical primaries; no simulation executed.')
    else:
        result = audit(args.campaign, args.repo, args.executable)
        if args.report:
            write_json(args.report, result)
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
