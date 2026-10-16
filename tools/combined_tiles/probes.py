#!/usr/bin/env python3
"""Prepare and audit bounded optical-interface probes; never launch Geant4."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from common import command_lines, digest, read_json, require, write_json

PROBE_SCHEMA = 'combined-tiles-physics-probes-v2'
READOUT_MODE = 'boundary-entry'
SCOPE = ('Optical boundary and two-piece electron accounting engineering checks only, '
         'separate from beta-spectrum results. '
         'Boundary counts include repeated interactions and are not first-crossing '
         'probabilities or a fit to Fresnel probabilities.')
LIMITATIONS = [
    'The index-matched film probe checks optical boundaries, not beta equivalence '
    'between a 16 mm monolith and a 16.1 mm assembly.',
    'Optical-parent scintillation and other descendants retain the inherited physics. '
    'Legacy generated_optical_photons is not the optical-primary count or all births.',
    'Finite overlap sampling and these selected ray directions do not prove every '
    'possible optical trajectory or every geometry edge.',
]
REGIONS = {'front': 0, 'back': 1, 'grease': 2, 'sipm0': 3, 'sipm1': 4,
           'world': 5, 'other': 6, 'outside': 7}
# Verified against the pinned Geant4 11.4.2 public header, not the old summary.
STATUS_REFERENCE = ('https://raw.githubusercontent.com/Geant4/geant4/v11.4.2/'
                    'source/processes/optical/include/G4OpBoundaryProcess.hh')
STATUSES = {'Undefined': 0, 'Transmission': 1, 'FresnelRefraction': 2,
            'FresnelReflection': 3, 'TotalInternalReflection': 4,
            'SameMaterial': 12, 'StepTooSmall': 13, 'NoRINDEX': 14}


def specifications(joint_center: float, gap: float) -> list[dict]:
    """Every source is safely inside its starting volume, never on a boundary."""
    require(gap > 0.02, 'probe geometry needs a gap wider than 20 micrometres')
    front, back = joint_center + gap / 2, joint_center - gap / 2
    angle = math.radians(75)
    entries = [
        ('front-to-joint', [0, 0, front + 0.01], [0, 0, -1], 'normal', 'front', 'grease', 50),
        ('joint-to-front', [0, 0, joint_center], [0, 0, 1], 'normal', 'grease', 'front', 50),
        ('back-to-joint', [0, 0, back - 0.01], [0, 0, 1], 'normal', 'back', 'grease', 50),
        ('joint-to-back', [0, 0, joint_center], [0, 0, -1], 'normal', 'grease', 'back', 50),
        ('front-to-joint-tir75', [0, 0, front + 0.01], [math.sin(angle), 0, -math.cos(angle)],
         'tir', 'front', 'grease', 50),
        ('index-match-front', [0, 0, front + 0.01], [0, 0, -1], 'index-match', 'front', 'grease', 50),
        ('index-match-back', [0, 0, back - 0.01], [0, 0, 1], 'index-match', 'back', 'grease', 50),
        ('seam-to-sensor0', [49.99, -25, joint_center], [1, 0, 0], 'sensor', 'grease', 'sipm0', 10),
        ('seam-to-sensor1', [49.99, 25, joint_center], [1, 0, 0], 'sensor', 'grease', 'sipm1', 10),
    ]
    probes = []
    for index, (name, position, direction, kind, start, target, events) in enumerate(entries):
        probes.append({'id': name, 'kind': kind, 'position_mm': position,
                       'direction': direction, 'polarization': [0, 1, 0],
                       'polarization_angle_deg': 90 if direction[2] < 0 else (-90 if direction[2] > 0 else 0),
                       'energy_ev': 3.0, 'particle': 'opticalphoton', 'events': events,
                       'expected_piece': None,
                       'target_interface': [start, target],
                       'target_sensor': int(target[-1]) if kind == 'sensor' else None,
                       'seeds': [26101073 + 107 * index, 96101071 + 109 * index],
                       'joint_rindex': 1.58 if kind == 'index-match' else 1.46,
                       'joint_abslength_mm': 3800.0 if kind == 'index-match' else None})
    for offset, piece in enumerate(('front', 'back')):
        index = len(probes)
        probes.append({'id': 'piece-' + piece, 'kind': 'piece-accounting',
                       'position_mm': [0, 0, joint_center + (4 + gap / 2) * (1 if offset == 0 else -1)],
                       'direction': [1, 0, 0], 'polarization': [0, 1, 0],
                       'polarization_angle_deg': None,
                       'energy_ev': 1e6, 'particle': 'e-', 'events': 5,
                       'expected_piece': piece, 'target_interface': None, 'target_sensor': None,
                       'seeds': [26101073 + 107 * index, 96101071 + 109 * index],
                       'joint_rindex': 1.46, 'joint_abslength_mm': None})
    require(sum(p['events'] for p in probes if p['particle'] == 'opticalphoton') <= 500,
            'optical-primary budget exceeded')
    require(sum(p['events'] for p in probes if p['particle'] == 'e-') == 10,
            'electron-accounting budget changed')
    return probes


def make_macro(smoke_macro: str, probe: dict) -> str:
    lines = command_lines(smoke_macro)
    require(sum(line == '/run/initialize' for line in lines) == 1, 'initialize must be unique')
    require('/opnovice2/combined/enabled true' in lines, 'base macro is not combined geometry')
    require('/opnovice2/combined/layers 8 0.1 8 mm' in lines, 'base macro must be joined 8+8 mm')
    readout = [line for line in lines if line.startswith('/opnovice2/sipm/readoutMode ')]
    require(readout == ['/opnovice2/sipm/readoutMode ' + READOUT_MODE],
            'probe base must explicitly select boundary-entry readout exactly once')
    require(lines.index(readout[0]) < lines.index('/run/initialize'),
            'readout mode must be selected before initialization')
    require('/opnovice2/boxProperty RINDEX 0.0000020 1.58 0.0000033 1.58' in lines,
            'index-match expectation needs the inherited EJ-200 index')
    require('/opnovice2/boxProperty ABSLENGTH 0.0000020 3800 0.0000033 3800' in lines,
            'index-match expectation needs the inherited EJ-200 attenuation length')
    require(not any(line.startswith('/control/execute') for line in lines),
            'nested macro execution is not allowed in a frozen probe')
    require(not any(line.startswith('/opnovice2/gun/randomDirection') for line in lines),
            'inherited randomDirection command would override the specified ray')
    kept = []
    for line in lines:
        command = line.split()[0]
        if (command.startswith(('/gps/', '/random/', '/geometry/test/'))
                or command in ('/run/beamOn', '/run/printProgress',
                               '/opnovice2/gun/electronEnergyMode', '/opnovice2/gun/optPhotonPolar')):
            continue
        if probe['kind'] == 'index-match' and (
                line.startswith('/opnovice2/greaseProperty RINDEX ')
                or line.startswith('/opnovice2/greaseProperty ABSLENGTH ')):
            continue
        if line == '/run/initialize' and probe['kind'] == 'index-match':
            kept += ['/opnovice2/greaseProperty RINDEX 0.0000020 1.58 0.0000033 1.58',
                     '/opnovice2/greaseProperty ABSLENGTH 0.0000020 3800 0.0000033 3800']
        kept.append(line)
    vector = lambda values: ' '.join(f'{value:.17g}' for value in values)
    # Each task starts a fresh process with the one constructor-created GPS
    # source. Every inherited /gps/ command was removed, so no source clear/add
    # or histogram-reset command is necessary here.
    kept += ['/gps/particle ' + probe['particle'],
             '/gps/number 1', '/gps/pos/type Point',
             '/gps/pos/centre ' + vector(probe['position_mm']) + ' mm',
             '/gps/direction ' + vector(probe['direction']),
             '/gps/polarization ' + vector(probe['polarization']),
             '/gps/ene/type Mono', '/gps/ene/mono ' + ('3 eV' if probe['particle'] == 'opticalphoton' else '1 MeV')]
    # PrimaryGeneratorAction overrides the GPS polarization on every optical
    # event. Its own fixed-angle command is necessary to prevent a random first
    # angle and to reproduce +Y polarization for these three ray orientations.
    if probe['particle'] == 'opticalphoton':
        kept.append(f"/opnovice2/gun/optPhotonPolar {probe['polarization_angle_deg']} deg")
    kept += [
             '/random/setSeeds 171007 271007', '/geometry/test/resolution 10000',
             '/geometry/test/run', '/random/setSeeds ' + vector(probe['seeds']),
             '/random/setSavingFlag true', '/random/setDirectoryName ./',
             '/run/printProgress 10', f"/run/beamOn {probe['events']}"]
    require(not any(line.startswith(('/gps/hist/', '/gps/energy ')) for line in kept),
            'inherited beta spectrum or energy remains')
    require(sum(line.startswith('/gps/particle ') for line in kept) == 1,
            'probe particle is ambiguous')
    return '# ' + PROBE_SCHEMA + ': ' + probe['id'] + '\n# ' + SCOPE + '\n' + '\n'.join(kept) + '\n'


def prepare(smoke: Path, output: Path) -> dict:
    smoke, output = smoke.resolve(), output.resolve()
    require(not output.exists(), f'refusing to overwrite existing output: {output}')
    smoke_manifest = read_json(smoke / 'manifest.json')
    require(smoke_manifest['phase'] == 'smoke', 'base campaign must be smoke')
    require(smoke_manifest['configuration'].get('readout_mode') == READOUT_MODE,
            'probes require a boundary-entry smoke campaign')
    bases = [task for task in smoke_manifest['tasks'] if task['case_id'] == 'joined8-8']
    require(len(bases) == 1, 'expected exactly one joined8-8 smoke task')
    base_task = bases[0]
    base_macro = smoke / base_task['id'] / 'run.mac'
    require(digest(base_macro) == base_task['macro_sha256'], 'smoke base macro changed')
    base_case = next(case for case in smoke_manifest['cases'] if case['id'] == 'joined8-8')
    require(base_case['front_mm'] == base_case['back_mm'] == 8 and base_case['gap_mm'] == 0.1,
            'unexpected joined8-8 geometry')
    repo = Path(smoke_manifest['source_root_at_preparation'])
    sources = dict(smoke_manifest['source_files_sha256'])
    for relative, expected in sources.items():
        require(digest(repo / relative) == expected, f'prepared smoke source drift: {relative}')
    sources['tools/combined_tiles/probes.py'] = digest(Path(__file__))
    probes = specifications(float(base_case['joint_center_z_mm']), float(base_case['gap_mm']))
    macros = [(probe, make_macro(base_macro.read_text(), probe)) for probe in probes]
    manifest = {
        'schema_version': PROBE_SCHEMA, 'phase': 'probe', 'scope': SCOPE,
        'readout_mode': READOUT_MODE,
        'limitations': LIMITATIONS, 'base_commit': smoke_manifest['base_commit'],
        'working_head': smoke_manifest['working_head'], 'source_root_at_preparation': str(repo),
        'source_files_sha256': sources,
        'base_smoke': {'path': str(smoke), 'manifest_sha256': digest(smoke / 'manifest.json'),
                       'task_id': base_task['id'], 'macro_sha256': digest(base_macro)},
        'probe_tool_sha256': digest(Path(__file__)),
        'boundary_contract': {'regions': REGIONS, 'statuses': STATUSES,
                              'status_reference': STATUS_REFERENCE},
        'configuration': {**smoke_manifest['configuration'],
                          'source': 'Case-specific monoenergetic point probes; see each case.probe for particle, position, direction and polarization.'},
        'optical_joint': {**smoke_manifest['optical_joint'],
                          'probe_overrides': 'The two index-match cases use n=1.58 and ABSLENGTH=3800 mm; other cases retain the smoke joint model.'},
        'execution': {'total_events': sum(p['events'] for p in probes),
                      'optical_primaries': sum(p['events'] for p in probes if p['particle'] == 'opticalphoton'),
                      'electron_accounting_primaries': 10,
                      'max_authorized_optical_primaries': 500,
                      'one_process_per_task': True, 'run_manager': 'Serial',
                      'retry_policy': 'none; any rerun requires a fresh output tree',
                      'geometry_sampling_seed': [171007, 271007],
                      'reset_seeds_after_geometry': True},
        'cases': [{**base_case, 'id': probe['id'], 'source_z_mm': probe['position_mm'][2], 'probe': probe}
                  for probe in probes],
        'tasks': [],
    }
    output.mkdir(parents=True)
    (output / 'reference-smoke.mac').write_bytes(base_macro.read_bytes())
    for probe, macro in macros:
        task_id = probe['id'] + '/block-00'
        folder = output / task_id
        folder.mkdir(parents=True)
        (folder / 'run.mac').write_text(macro)
        manifest['tasks'].append({'id': task_id, 'case_id': probe['id'], 'block': 0,
                                  'events': probe['events'], 'seeds': probe['seeds'],
                                  'macro_sha256': digest(folder / 'run.mac'), 'probe': probe})
    write_json(output / 'manifest.json', manifest)
    return manifest


def audit_tables(tables: dict, probe: dict) -> dict:
    """Validate every diagnostic event without applying the beta-spectrum contract."""
    import numpy as np
    from audit import COUNT_FIELDS, EDEP_COMPONENTS, counts, equal

    a, c, b = (tables[key] for key in ('scan', 'combined_events', 'combined_boundaries'))
    n = probe['events']
    for name, table in [('scan', a), ('combined_events', c)]:
        require(np.array_equal(table['event_id'], np.arange(n)), f'{name}: event coverage')
        require(all(len(value) == n for value in table.values()), f'{name}: column lengths')
    counts(a, COUNT_FIELDS, 'scan')
    equal(a['primary_kinetic_energy_mev'], probe['energy_ev'] * 1e-6, 'probe source energy', atol=1e-14)
    for axis, coordinate in zip('xyz', probe['position_mm']):
        equal(a[f'shoot_{axis}_mm'], coordinate, 'probe source ' + axis)
    require(np.isin(a['hit_valid'], [0, 1]).all(), 'invalid hit flags')
    g, d = a['generated_optical_photons'], a['sipm_detected_photons']
    equal(d, a['sipm_sensor_0_detected_photons'] + a['sipm_sensor_1_detected_photons'], 'sensor sum')
    for sensor in (2, 3):
        equal(a[f'sipm_sensor_{sensor}_detected_photons'], 0, 'inactive sensor')
    valid = g > 0
    require(np.array_equal(a['collection_efficiency_valid'], valid), 'legacy efficiency flags')
    equal(a['collection_efficiency'][valid], d[valid] / g[valid], 'legacy D/G')
    require(np.isnan(a['collection_efficiency'][~valid]).all(), 'legacy zero-denominator NaN')
    for field in ('tile_edep_mev', *EDEP_COMPONENTS):
        require(np.isfinite(a[field]).all() and (a[field] >= 0).all(), 'invalid ' + field)
    equal(a['tile_edep_mev'], sum(a[field] for field in EDEP_COMPONENTS), 'legacy edep components')
    equal(a['steel_edep_mev'], 0, 'absent steel')
    transfer = [f'lineage_{piece}_to_sensor{i}' for piece in ('front', 'back') for i in (0, 1)]
    unknown = [f'unknown_to_sensor{i}' for i in (0, 1)]
    births = [f'all_births_{piece}' for piece in ('front', 'back', 'joint', 'other')]
    counts(c, ['front_generated', 'back_generated', *transfer, *unknown, *births,
               'optical_parent_births', 'boundary_steps', 'joint_boundary_steps', 'no_rindex_steps'],
           'combined_events')
    for sensor in (0, 1):
        equal(c[f'lineage_front_to_sensor{sensor}'] + c[f'lineage_back_to_sensor{sensor}']
              + c[f'unknown_to_sensor{sensor}'], a[f'sipm_sensor_{sensor}_detected_photons'],
              'lineage transfer sum')
    # Grease-born primaries legitimately have unknown plastic lineage. Do not
    # enforce the beta-only unknown-origin=0 gate or pretend they contribute G.
    all_births = sum(c[key] for key in births)
    require((c['optical_parent_births'] <= all_births).all(), 'parent birth counts exceed all births')
    primary_optical = 1 if probe['particle'] == 'opticalphoton' else 0
    require((d <= primary_optical + all_births).all(), 'more detections than primary plus born optical tracks')
    for piece in ('front', 'back', 'joint'):
        total, optical = c[f'{piece}_edep_mev'], c[f'{piece}_optical_edep_mev']
        require(np.isfinite(total).all() and np.isfinite(optical).all()
                and (total >= 0).all() and (optical >= 0).all(), 'invalid combined energy')
        require((optical <= total + 1e-12).all(), 'optical energy exceeds total')
    equal(c['front_edep_mev'] + c['back_edep_mev'], a['tile_edep_mev'], 'per-piece energy')
    counts(b, ('event_id', 'pre_region', 'post_region', 'status', 'count'), 'combined_boundaries')
    require(all(len(value) == len(b['event_id']) for value in b.values()), 'boundary column lengths')
    require((b['event_id'] < n).all() and (b['pre_region'] <= 7).all()
            and (b['post_region'] <= 7).all() and (b['status'] <= 42).all(), 'boundary range')
    require((b['count'] > 0).all(), 'sparse boundary count must be positive')
    keys = list(zip(b['event_id'], b['pre_region'], b['post_region'], b['status']))
    require(len(keys) == len(set(keys)), 'duplicate sparse boundary key')
    joint = (b['pre_region'] == 2) | (b['post_region'] == 2)
    for mask, field in [(np.ones(len(b['event_id']), dtype=bool), 'boundary_steps'),
                        (joint, 'joint_boundary_steps'),
                        (b['status'] == 14, 'no_rindex_steps')]:
        event_counts = np.bincount(b['event_id'][mask].astype(int), weights=b['count'][mask], minlength=n)
        equal(event_counts, c[field], 'boundary event reconciliation ' + field)
    require(not np.any(b['status'] == 14) and np.all(c['no_rindex_steps'] == 0), 'NoRINDEX')
    require(not np.any(b['status'] == 0), 'Undefined boundary process status')
    successful_entries, reflected_without_entry = [], []
    from_nonsensor = ~np.isin(b['pre_region'], [3, 4])
    for sensor in (0, 1):
        candidate = from_nonsensor & (b['post_region'] == 3 + sensor)
        successful = candidate & np.isin(b['status'], [1, 2, 12])
        per_event = np.bincount(b['event_id'][successful].astype(int),
                                weights=b['count'][successful], minlength=n)
        # The rejected v1 run counted reflected photons during the following
        # StepTooSmall recovery step. Neither status can establish sensor entry.
        # A photon may reflect first and enter later; only the later successful
        # transmission contributes here, so it is still counted exactly once.
        require(np.array_equal(a[f'sipm_sensor_{sensor}_detected_photons'], per_event),
                f'sensor {sensor}: D must equal successful non-sensor boundary entries per event')
        reflected = candidate & np.isin(b['status'], [3, 4])
        reflection_events = np.bincount(b['event_id'][reflected].astype(int),
                                       weights=b['count'][reflected], minlength=n)
        successful_entries.append(int(per_event.sum()))
        reflected_without_entry.append(np.flatnonzero((reflection_events > 0) & (per_event == 0)).tolist())
    internal = ((np.isin(b['pre_region'], [0, 1]) & (b['post_region'] == 2))
                | ((b['pre_region'] == 2) & np.isin(b['post_region'], [0, 1])))
    require(np.isin(b['status'][internal], [1, 2, 3, 4, 12, 13]).all(),
            'internal interface contains an unexpected painted, absorption or detection status')
    target = np.zeros(len(b['event_id']), dtype=bool)
    if probe['target_interface'] is not None:
        pre, post = (REGIONS[region] for region in probe['target_interface'])
        target = (b['pre_region'] == pre) & (b['post_region'] == post)
    refracted = int(b['count'][target & (b['status'] == 2)].sum())
    reflected = int(b['count'][target & (b['status'] == 3)].sum())
    tir = int(b['count'][target & (b['status'] == 4)].sum())
    if probe['target_interface'] is not None:
        require(int(b['count'][target].sum()) > 0, 'target interface was never observed')
    if probe['kind'] in ('normal', 'index-match'):
        require(refracted > 0, 'normal probe has no target Fresnel refraction')
    if probe['kind'] == 'tir':
        require(tir > 0, '75 degree probe has no target total internal reflection')
    if probe['kind'] == 'index-match':
        require(not np.any(np.isin(b['status'][internal], [3, 4])),
                'index-matched internal interface reflects')
    if probe['kind'] == 'sensor':
        require(refracted > 0, 'seam-to-sensor target has no Fresnel refraction')
        require(int(a[f"sipm_sensor_{probe['target_sensor']}_detected_photons"].sum()) > 0,
                'seam probe never reached its intended sensor')
    if probe['kind'] == 'piece-accounting':
        piece = probe['expected_piece']
        other = 'back' if piece == 'front' else 'front'
        require(int(c[f'{piece}_generated'].sum()) > 0, 'target piece produced no legacy photons')
        equal(c[f'{other}_generated'], 0, 'opposite piece legacy generation')
        equal(c['front_generated'] + c['back_generated'], g, 'piece legacy generation sum')
        nonoptical = c[f'{piece}_edep_mev'] - c[f'{piece}_optical_edep_mev']
        require(float(nonoptical.sum()) > 0, 'target piece has no nonoptical deposition')
        equal(c[f'{other}_edep_mev'] - c[f'{other}_optical_edep_mev'], 0,
              'opposite piece nonoptical deposition', atol=1e-10)
        require(sum(int(c[f'lineage_{piece}_to_sensor{i}'].sum()) for i in (0, 1)) > 0,
                'target piece has no collected optical lineage')
        for sensor in (0, 1):
            equal(c[f'lineage_{other}_to_sensor{sensor}'], 0, 'opposite optical lineage')
            equal(c[f'unknown_to_sensor{sensor}'], 0, 'unknown electron-produced optical lineage')
    return {'events': n, 'target_interface': probe['target_interface'],
            'target_FresnelRefraction': refracted, 'target_FresnelReflection': reflected,
            'target_TotalInternalReflection': tir,
            'internal_status_counts': {str(status): int(b['count'][internal & (b['status'] == status)].sum())
                                       for status in sorted(set(int(v) for v in b['status'][internal]))},
            'sensor_totals': [int(a[f'sipm_sensor_{i}_detected_photons'].sum()) for i in (0, 1)],
            'sensor_successful_boundary_entries': successful_entries,
            'sensor_reflected_without_entry_event_ids': reflected_without_entry,
            'zero_detected_events': int((d == 0).sum()), 'legacy_generated': int(g.sum()),
            'primary_particle': probe['particle'], 'primary_energy_ev': probe['energy_ev'],
            'legacy_generated_by_piece': {piece: int(c[f'{piece}_generated'].sum()) for piece in ('front', 'back')},
            'nonoptical_edep_mev_by_piece': {piece: float((c[f'{piece}_edep_mev'] - c[f'{piece}_optical_edep_mev']).sum())
                                            for piece in ('front', 'back', 'joint')},
            'secondary_births': {key: int(c[key].sum()) for key in (*births, 'optical_parent_births')},
            'lineage_transfers': {key: int(c[key].sum()) for key in (*transfer, *unknown)},
            'boundary_steps': int(c['boundary_steps'].sum()), 'no_rindex_steps': 0}


def validate_inputs(campaign: Path, repo: Path) -> dict:
    m = read_json(campaign / 'manifest.json')
    require(m['schema_version'] == PROBE_SCHEMA and m['phase'] == 'probe', 'not a physics probe manifest')
    require(m.get('readout_mode') == READOUT_MODE and m['configuration'].get('readout_mode') == READOUT_MODE,
            'probe readout contract is not boundary-entry')
    require(m['boundary_contract']['regions'] == REGIONS and m['boundary_contract']['statuses'] == STATUSES,
            'boundary contract changed')
    for relative, expected in m['source_files_sha256'].items():
        require(digest(repo / relative) == expected, 'frozen source changed: ' + relative)
    require(digest(repo / 'tools/combined_tiles/probes.py') == m['probe_tool_sha256'], 'probe tool changed')
    reference = campaign / 'reference-smoke.mac'
    require(digest(reference) == m['base_smoke']['macro_sha256'], 'reference smoke macro changed')
    probes = specifications(0.0, 0.1)
    require(len(m['tasks']) == len(probes), 'incomplete probe matrix')
    require(m['execution']['total_events'] == sum(p['events'] for p in probes) == 380, 'probe primary budget')
    require(m['execution']['optical_primaries'] == 370 and m['execution']['electron_accounting_primaries'] == 10,
            'probe particle budgets changed')
    require(len(m['cases']) == len(probes), 'probe case matrix differs')
    for task, case, expected in zip(m['tasks'], m['cases'], probes):
        require(task['probe'] == expected and case['probe'] == expected, 'probe specification changed')
        require(task['id'] == expected['id'] + '/block-00' and task['case_id'] == expected['id']
                and task['block'] == 0 and case['id'] == expected['id'], 'probe task identity changed')
        require(task['events'] == expected['events'] and task['seeds'] == expected['seeds'], 'event/seed plan changed')
        macro = campaign / task['id'] / 'run.mac'
        require(digest(macro) == task['macro_sha256'], 'probe macro hash mismatch')
        require(macro.read_text() == make_macro(reference.read_text(), expected), 'probe macro contract mismatch')
    return m


def audit(campaign: Path, repo: Path, executable: Path | None = None) -> dict:
    import uproot
    from audit import EMPTY_TREES, arrays, audit_log, audit_readout, audit_summary

    m = validate_inputs(campaign, repo)
    results, binaries = [], set()
    for task, case in zip(m['tasks'], m['cases']):
        folder = campaign / task['id']
        receipt = read_json(folder / 'receipt.json')
        require(receipt['exit_code'] == 0, 'probe process did not exit successfully')
        require(receipt.get('readout_mode') == READOUT_MODE, 'probe receipt readout mode differs')
        for key, path in [('macro_sha256', folder / 'run.mac'), ('log_sha256', folder / 'simulation.log'),
                          ('manifest_sha256', campaign / 'manifest.json')]:
            require(receipt[key] == digest(path), 'receipt mismatch: ' + key)
        binaries.add(receipt['executable_sha256'])
        warnings = audit_log((folder / 'simulation.log').read_text(), case)
        with uproot.open(folder / 'result.root') as root:
            tables = {name: arrays(root[name]) for name in ('scan', 'combined_events', 'combined_boundaries',
                                                           'readout_events')}
            require(all(root[name].num_entries == 0 for name in (*EMPTY_TREES, 'decay_betas')),
                    'unrelated event tables are not empty')
        result = audit_tables(tables, task['probe'])
        result['readout'] = audit_readout(tables, task['events'], READOUT_MODE)
        audit_summary(folder / 'result_summary.csv', tables['scan'], task['events'])
        rng = sorted(folder.glob('*.rndm'))
        require(bool(rng), 'missing random-state evidence')
        result.update(task_id=task['id'], warnings=warnings,
                      files_sha256={path.name: digest(path) for path in
                                    [folder / name for name in ('run.mac', 'receipt.json', 'simulation.log',
                                                                 'result.root', 'result_summary.csv')] + rng})
        results.append(result)
    require(len(binaries) == 1, 'probes mixed executable identities')
    if executable is not None:
        require(binaries == {digest(executable)}, 'probe executable identity differs')
    return {'schema_version': PROBE_SCHEMA, 'status': 'passed', 'scope': SCOPE,
            'readout_mode': READOUT_MODE,
            'limitations': LIMITATIONS, 'manifest_sha256': digest(campaign / 'manifest.json'),
            'executable_sha256': next(iter(binaries)),
            'total_optical_primaries': sum(r['events'] for r in results if r['primary_particle'] == 'opticalphoton'),
            'total_electron_accounting_primaries': sum(r['events'] for r in results if r['primary_particle'] == 'e-'),
            'status_reference': STATUS_REFERENCE, 'tasks': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--smoke-campaign', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('audit')
    check.add_argument('--campaign', type=Path, required=True)
    check.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    check.add_argument('--executable', type=Path)
    check.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare(args.smoke_campaign, args.output)
        print(f"Prepared {len(result['tasks'])} diagnostic tasks: 370 optical + 10 electron primaries; "
              'no simulation executed.')
    else:
        result = audit(args.campaign.resolve(), args.repo.resolve(), args.executable)
        if args.report:
            write_json(args.report, result)
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
