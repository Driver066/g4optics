#!/usr/bin/env python3
"""Audit frozen inputs, process receipts, geometry and event-level study outputs."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re

import numpy as np
import uproot

from common import (CASES, SCHEMA, SPECTRUM, case_geometry, command_lines, digest,
                    expected_seeds, read_json, require, write_json)
from prepare import make_macro

EMPTY_TREES = ('stack_layers', 'stack_transfers', 'layout_layers', 'layout_sensors', 'layout_transfers')
COUNT_FIELDS = ('generated_optical_photons', 'scintillation_photons', 'cerenkov_photons',
                'sipm_detected_photons', *(f'sipm_sensor_{i}_detected_photons' for i in range(4)))
EDEP_COMPONENTS = ('electron_tile_edep_mev', 'proton_tile_edep_mev',
                   'other_charged_tile_edep_mev', 'neutral_tile_edep_mev')


def equal(actual, expected, name, *, atol=1e-9):
    require(np.allclose(actual, expected, rtol=1e-10, atol=atol, equal_nan=False), f'mismatch: {name}')


def counts(a, fields, name):
    for key in fields:
        require(key in a, f'{name}: missing {key}')
        values = a[key]
        require(np.isfinite(values).all() and (values >= 0).all() and np.equal(values, np.floor(values)).all(),
                f'{name}: invalid counts {key}')


def arrays(tree):
    result = tree.arrays(library='np')
    return {key: result[key] for key in (result.dtype.names if isinstance(result, np.ndarray) else result)}


def audit_readout(tables: dict, events: int, readout_mode: str = 'boundary-entry') -> dict:
    require('readout_events' in tables, 'missing readout_events diagnostics')
    r = tables['readout_events']
    if readout_mode == 'legacy':
        require(len(r['event_id']) == 0, 'legacy unexpectedly fills readout_events')
        return {'mode': 'legacy', 'scope': 'historical regression only; known reflection tally defect'}
    require(readout_mode == 'boundary-entry', 'invalid readout mode')
    require(np.array_equal(r['event_id'], np.arange(events)), 'readout event coverage')
    require(all(len(v) == events for v in r.values()), 'readout column lengths')
    fields = [f'sensor{i}_{suffix}' for i in range(2) for suffix in ('entries', 'reflections', 'step_too_small')]
    counts(r, [*fields, 'no_rindex_steps'], 'readout_events')
    for i in range(2):
        equal(r[f'sensor{i}_entries'], tables['scan'][f'sipm_sensor_{i}_detected_photons'],
              f'readout true entries sensor {i}')
    require((r['no_rindex_steps'] == 0).all(), 'readout all-boundary NoRINDEX detected')
    return {'mode': readout_mode, **{key: int(r[key].sum()) for key in (*fields, 'no_rindex_steps')}}


def audit_tables(tables: dict, case: dict, events: int, readout_mode: str = 'boundary-entry') -> dict:
    require(readout_mode in ('boundary-entry', 'legacy'), 'invalid readout mode')
    require('scan' in tables, 'missing scan tree')
    a = tables['scan']
    require(np.array_equal(a['event_id'], np.arange(events)), 'missing/duplicate/out-of-order scan event')
    require(all(len(v) == events for v in a.values()), 'scan columns differ in row count')
    counts(a, COUNT_FIELDS, 'scan')
    energy = a['primary_kinetic_energy_mev']
    require(np.isfinite(energy).all() and (energy >= 0).all() and (energy <= 2.28).all(), 'primary energy outside spectrum')
    require(events <= 1 or np.unique(energy).size > 1, 'unexpected monoenergetic source')
    equal(a['shoot_x_mm'], 0, 'source x'); equal(a['shoot_y_mm'], 0, 'source y')
    equal(a['shoot_z_mm'], case['source_z_mm'], 'source outside physical envelope')
    require(np.isin(a['hit_valid'], [0, 1]).all(), 'invalid hit flag')
    entered = a['hit_valid'].astype(bool)
    # Do not discard non-entering/zero-light beta events from response statistics.
    equal(a['hit_z_mm'][entered], case['envelope_thickness_mm'] / 2, 'entrance face', atol=1e-7)
    g, d = a['generated_optical_photons'], a['sipm_detected_photons']
    require(np.array_equal(g, a['scintillation_photons'] + a['cerenkov_photons']), 'legacy generation components')
    require((a['cerenkov_photons'] == 0).all(), 'Cerenkov unexpectedly enabled')
    require(np.array_equal(d, a['sipm_sensor_0_detected_photons'] + a['sipm_sensor_1_detected_photons']), 'sensor sums')
    require((a['sipm_sensor_2_detected_photons'] == 0).all() and (a['sipm_sensor_3_detected_photons'] == 0).all(), 'inactive sensors')
    valid = g > 0
    require(np.array_equal(a['collection_efficiency_valid'], valid), 'efficiency validity flags')
    equal(a['collection_efficiency'][valid], d[valid] / g[valid], 'legacy efficiency')
    require(np.isnan(a['collection_efficiency'][~valid]).all(), 'zero denominator must remain NaN')
    for key in ('tile_edep_mev', *EDEP_COMPONENTS):
        require(np.isfinite(a[key]).all() and (a[key] >= 0).all(), f'invalid {key}')
    equal(a['tile_edep_mev'], sum(a[k] for k in EDEP_COMPONENTS), 'tile energy components')
    equal(a['steel_edep_mev'], 0, 'no absorber energy')
    result = {'readout_mode': readout_mode, 'events': events, 'primary_entered': int(entered.sum()),
              'generated': int(g.sum()), 'detected': int(d.sum()),
              'sensor_totals': [int(a[f'sipm_sensor_{i}_detected_photons'].sum()) for i in range(2)],
              'zero_generated_events': int((g == 0).sum()), 'zero_detected_events': int((d == 0).sum()),
              'D_per_event': float(d.mean()),
              'legacy_D_over_G': float(d.sum() / g.sum()) if g.sum() else None,
              'primary_energy_range_mev': [float(energy.min()), float(energy.max())],
              'tile_edep_sum_mev_including_optical': float(a['tile_edep_mev'].sum())}
    result['readout'] = audit_readout(tables, events, readout_mode)
    if not case['combined']:
        for tree in ('combined_events', 'combined_boundaries'):
            require(tree in tables and len(tables[tree]['event_id']) == 0, f'monolith unexpectedly fills {tree}')
        return result
    require('combined_events' in tables and 'combined_boundaries' in tables, 'missing combined diagnostics')
    c, b = tables['combined_events'], tables['combined_boundaries']
    require(np.array_equal(c['event_id'], np.arange(events)), 'combined event coverage')
    transfer_keys = [f'lineage_{piece}_to_sensor{i}' for piece in ('front', 'back') for i in range(2)]
    unknown = [f'unknown_to_sensor{i}' for i in range(2)]
    birth_keys = [f'all_births_{piece}' for piece in ('front', 'back', 'joint', 'other')]
    counts(c, ['front_generated', 'back_generated', *transfer_keys, *unknown, *birth_keys,
               'optical_parent_births', 'boundary_steps', 'joint_boundary_steps', 'no_rindex_steps'], 'combined_events')
    require(np.array_equal(c['front_generated'] + c['back_generated'], g), 'per-piece legacy generation sums')
    for i in range(2):
        require(np.array_equal(c[f'lineage_front_to_sensor{i}'] + c[f'lineage_back_to_sensor{i}'] + c[f'unknown_to_sensor{i}'],
                               a[f'sipm_sensor_{i}_detected_photons']), 'lineage transfer sums')
        require((c[f'unknown_to_sensor{i}'] == 0).all(), 'unknown detected optical lineage')
    require((c['no_rindex_steps'] == 0).all(), 'all-boundary NoRINDEX detected')
    for piece in ('front', 'back', 'joint'):
        total, optical = c[f'{piece}_edep_mev'], c[f'{piece}_optical_edep_mev']
        require(np.isfinite(total).all() and np.isfinite(optical).all() and (total >= 0).all() and (optical >= 0).all(), 'invalid combined edep')
        require((optical <= total + 1e-10).all(), 'optical edep exceeds total')
    equal(c['front_edep_mev'] + c['back_edep_mev'], a['tile_edep_mev'], 'per-piece tile edep')
    counts(b, ['event_id', 'pre_region', 'post_region', 'status', 'count'], 'combined_boundaries')
    require((b['event_id'] < events).all() and (b['pre_region'] <= 7).all() and (b['post_region'] <= 7).all(), 'invalid boundary region/event')
    require((b['count'] > 0).all(), 'sparse boundary table contains zero count')
    keys = list(zip(b['event_id'], b['pre_region'], b['post_region'], b['status']))
    require(len(keys) == len(set(keys)), 'duplicate sparse boundary key')
    totals = np.bincount(b['event_id'].astype(int), weights=b['count'], minlength=events)
    joint = (b['pre_region'] == 2) | (b['post_region'] == 2)
    joint_totals = np.bincount(b['event_id'][joint].astype(int), weights=b['count'][joint], minlength=events)
    equal(totals, c['boundary_steps'], 'all boundary totals'); equal(joint_totals, c['joint_boundary_steps'], 'joint boundary totals')
    if readout_mode == 'boundary-entry':
        successful = np.isin(b['status'], [1, 2, 12]) & ~np.isin(b['pre_region'], [3, 4])
        for sensor in range(2):
            mask = successful & (b['post_region'] == 3 + sensor)
            entries = np.bincount(b['event_id'][mask].astype(int), weights=b['count'][mask], minlength=events)
            equal(entries, a[f'sipm_sensor_{sensor}_detected_photons'], f'true boundary-entry sensor {sensor}')
    # Geant4 11.4.2 G4OpBoundaryProcessStatus: NoRINDEX=14. The per-event counter is independent.
    require(not np.any(b['status'] == 14), 'sparse boundary NoRINDEX detected')
    result['diagnostics'] = {
        'birth_counts': {key: int(c[key].sum()) for key in (*birth_keys, 'optical_parent_births')},
        'legacy_generation_by_piece': [int(c['front_generated'].sum()), int(c['back_generated'].sum())],
        'lineage_transfers': {key: int(c[key].sum()) for key in transfer_keys},
        'boundary_steps': int(c['boundary_steps'].sum()), 'joint_boundary_steps': int(c['joint_boundary_steps'].sum()),
        'no_rindex_steps': 0,
        'edep_mev': {piece: {'total': float(c[f'{piece}_edep_mev'].sum()),
                            'optical': float(c[f'{piece}_optical_edep_mev'].sum()),
                            'nonoptical': float((c[f'{piece}_edep_mev'] - c[f'{piece}_optical_edep_mev']).sum())}
                     for piece in ('front', 'back', 'joint')},
        'caveat': 'Birth place differs from inherited optical lineage; boundary post_region is candidate neighbor, not proof of transmission. No full optical terminal closure claimed.'}
    return result


def audit_summary(path: Path, a: dict, events: int):
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    require(len(rows) == 1, 'summary must contain one row')
    s = rows[0]
    require(int(s['events']) == int(s['committed_events']) == events, 'summary event count')
    for key in ('generated_optical_photons', 'scintillation_photons', 'cerenkov_photons', 'sipm_detected_photons'):
        require(int(s[key]) == int(a[key].sum()), f'summary mismatch {key}')
    equal(float(s['tile_edep_sum_mev']), a['tile_edep_mev'].sum(), 'summary edep')
    g, d = a['generated_optical_photons'].sum(), a['sipm_detected_photons'].sum()
    require(int(s['collection_efficiency_valid']) == int(g > 0), 'summary efficiency valid')
    if g:
        equal(float(s['collection_efficiency']), d / g, 'summary efficiency')
    else:
        require(np.isnan(float(s['collection_efficiency'])), 'summary invalid efficiency must be NaN')
    equal(float(s['net_sipm_photons_per_event']), d / events, 'summary response per event')


def compare_baseline_files(reference: Path, current: Path):
    for name in ('result_summary.csv', 'currentEvent.rndm', 'currentRun.rndm'):
        require((reference / name).read_bytes() == (current / name).read_bytes(), f'monolith regression byte mismatch: {name}')
    with uproot.open(reference / 'result.root') as old, uproot.open(current / 'result.root') as new:
        old_hists = {key: cls for key, cls in old.classnames(cycle=False).items() if cls.startswith('TH')}
        new_hists = {key: cls for key, cls in new.classnames(cycle=False).items() if cls.startswith('TH')}
        require(old_hists == new_hists, 'monolith regression histogram set changed')
        for name in old_hists:
            left, right = old[name], new[name]
            for getter in ('values', 'variances'):
                require(np.array_equal(getattr(left, getter)(flow=True), getattr(right, getter)(flow=True), equal_nan=True),
                        f'histogram regression: {name} {getter}')
            for a, b in zip(left.axes, right.axes):
                require(np.array_equal(a.edges(flow=True), b.edges(flow=True), equal_nan=True), f'histogram axis regression: {name}')
            for field in ('fEntries', 'fTsumw', 'fTsumw2', 'fTsumwx', 'fTsumwx2', 'fMinimum', 'fMaximum', 'fNormFactor'):
                require(left.member(field) == right.member(field), f'histogram statistic regression: {name} {field}')


def audit_log(text: str, case: dict) -> list[str]:
    for forbidden in ('COMMAND NOT FOUND', '***** Illegal', 'FatalException', 'Overlap is detected',
                      'Segmentation fault', 'Aborted', 'Run must be terminated', 'Batch is interrupted'):
        require(forbidden not in text, f'log contains {forbidden}')
    codes = re.findall(r'\*\*\* G4Exception : ([^\s]+)', text)
    require(set(codes) <= {'Analysis_W001'}, f'unexpected warnings: {codes}')
    if codes:
        require('Merging ntuples is not applicable in sequential application.' in text, 'unexpected Analysis_W001')
    # A positive inherited first-boundary count is an error; absent output alone is not complete history evidence.
    require(not re.search(r'No\s*RINDEX\s*:\s*[1-9]', text), 'logged NoRINDEX')
    require('Geometry overlaps check completed !' in text, 'missing geometry check completion')
    placements = [line for line in text.splitlines() if line.startswith('SiPM placement:')]
    require(len(placements) == 2, 'expected exactly two runtime sensor placements')
    for i, y in enumerate((-25, 25)):
        require(any(f'copy={i}, face=+X, local=({y},0,0) mm, world=(50.25,{y},0) mm' in line for line in placements), 'runtime sensor placement mismatch')
        require(f'Checking overlaps for volume SiPM:{i} (G4Box) ... OK!' in text, 'missing sensor overlap check')
    if not case['combined']:
        require('Checking overlaps for volume Tank:0 (G4Box) ... OK!' in text, 'missing tile overlap check')
    else:
        half = case['envelope_thickness_mm'] / 2
        joint_center = case['joint_center_z_mm']
        low, high = joint_center - case['gap_mm'] / 2, joint_center + case['gap_mm'] / 2
        for i, name, center, thickness in ((0, 'TankFront', case['front_center_z_mm'], case['front_mm']),
                                            (1, 'TankBack', case['back_center_z_mm'], case['back_mm'])):
            expected = (f'Combined tile placement: piece={i}, name={name}, center_z={center:g} mm, '
                        f'z_min={center-thickness/2:g} mm, z_max={center+thickness/2:g} mm, '
                        f'full_size=(100,100,{thickness:g}) mm, overlap_check=true')
            require(expected in text, 'combined piece placement mismatch')
            require(f'Checking overlaps for volume {name}:{i} (G4Box) ... OK!' in text, 'missing combined piece overlap')
        require((f'Combined joint placement: material=EJ550_Grease, center_z={joint_center:g} mm, '
                 f'z_min={low:g} mm, z_max={high:g} mm, thickness={case["gap_mm"]:g} mm, overlap_check=true') in text,
                'combined joint placement mismatch')
        require('Checking overlaps for volume CombinedGrease:0 (G4Box) ... OK!' in text, 'missing grease overlap check')
        require((f'Combined envelope: active_thickness={case["active_thickness_mm"]:g} mm, '
                 f'full_thickness={case["envelope_thickness_mm"]:g} mm, z_min={-half:g} mm, z_max={half:g} mm, '
                 'front=+Z, sensor_groups=1, sensors=2') in text, 'combined envelope mismatch')
        def contact(a, b): return 2.4 * max(0, min(1.2, b) - max(-1.2, a))
        for i, y in enumerate((-25, 25)):
            expected = (f'Combined sensor contact: copy={i}, center=(50.25,{y},0) mm, face_x=50 mm, '
                        f'front_area={contact(high, half):g} mm2, joint_area={contact(low, high):g} mm2, '
                        f'back_area={contact(-half, low):g} mm2, overlap_check=true')
            require(expected in text, 'sensor contact-area mismatch')
    return codes


def validate_inputs(campaign: Path, repo: Path) -> dict:
    m = read_json(campaign / 'manifest.json')
    require(m['schema_version'] == SCHEMA, 'unsupported manifest schema')
    require(m['phase'] in ('geometry', 'smoke', 'explore'), 'invalid phase')
    mode = m['configuration']['readout_mode']
    require(mode in ('boundary-entry', 'legacy'), 'invalid readout mode')
    require(m['phase'] != 'explore' or mode == 'boundary-entry', 'exploration requires boundary-entry readout')
    require(not m['baseline_regression'] or mode == 'legacy', 'historical regression requires legacy readout')
    require(m['cases'] == [case_geometry(case) for case in CASES], 'case matrix changed')
    for relative, expected in m['source_files_sha256'].items():
        require(digest(repo / relative) == expected, f'frozen source changed: {relative}')
    events, blocks = {'geometry': (0, 1), 'smoke': (10, 1), 'explore': (100, 2)}[m['phase']]
    require(len(m['tasks']) == len(CASES) * blocks, 'incomplete fixed task matrix')
    require(len({t['id'] for t in m['tasks']}) == len(m['tasks']), 'duplicate task ID')
    expected_ids = {f"{c['id']}/block-{b:02d}" for c in m['cases'] for b in range(blocks)}
    require({t['id'] for t in m['tasks']} == expected_ids, 'unexpected tasks')
    for task in m['tasks']:
        case = next(c for c in m['cases'] if c['id'] == task['case_id'])
        require(task['events'] == events and task['seeds'] == expected_seeds(case, m['phase'], task['block']), 'fixed event/seed plan changed')
        macro = campaign / task['id'] / 'run.mac'
        require(digest(macro) == task['macro_sha256'], 'macro hash mismatch')
        require(macro.read_text() == make_macro(repo, case, events, task['seeds'], mode), 'macro differs from frozen scientific contract')
    return m


def audit(campaign: Path, repo: Path, executable: Path | None = None, baseline: Path | None = None) -> dict:
    m = validate_inputs(campaign, repo)
    manifest_hash = digest(campaign / 'manifest.json')
    records, binary_hashes = [], set()
    for task in m['tasks']:
        case = next(c for c in m['cases'] if c['id'] == task['case_id'])
        folder = campaign / task['id']; receipt = read_json(folder / 'receipt.json')
        require(receipt['exit_code'] == 0, 'simulation did not exit successfully')
        require(receipt['readout_mode'] == m['configuration']['readout_mode'], 'receipt readout mode differs')
        for field, path in [('macro_sha256', folder / 'run.mac'), ('log_sha256', folder / 'simulation.log'), ('manifest_sha256', campaign / 'manifest.json')]:
            require(receipt[field] == digest(path), f'receipt mismatch: {field}')
        binary_hashes.add(receipt['executable_sha256'])
        record = {'task_id': task['id'], 'case_id': case['id'], 'block': task['block'],
                  'warnings': audit_log((folder / 'simulation.log').read_text(), case),
                  'files_sha256': {name: digest(folder / name) for name in ('run.mac', 'simulation.log', 'receipt.json')}}
        if task['events']:
            with uproot.open(folder / 'result.root') as root:
                tables = {name: arrays(root[name]) for name in ('scan', 'combined_events', 'combined_boundaries', 'readout_events')}
                require(all(root[name].num_entries == 0 for name in EMPTY_TREES), 'unrelated stack tables are not empty')
            record.update(audit_tables(tables, case, task['events'], m['configuration']['readout_mode']))
            audit_summary(folder / 'result_summary.csv', tables['scan'], task['events'])
            require(bool(list(folder.glob('*.rndm'))), 'missing random state evidence')
            for name in ('result.root', 'result_summary.csv'):
                record['files_sha256'][name] = digest(folder / name)
            if m['baseline_regression'] and not case['combined']:
                reference = baseline or Path(m['baseline_regression']['path'])
                for relative, expected in m['baseline_regression']['files_sha256'].items():
                    require(digest(reference / relative) == expected, 'frozen historical baseline changed')
                old_folder = reference / f"monolith-{int(case['active_thickness_mm'])}/smoke"
                with uproot.open(old_folder / 'result.root') as root:
                    old = arrays(root['scan'])
                require(set(old) == set(tables['scan']), 'monolith scan schema changed')
                for key, value in old.items():
                    require(np.array_equal(value, tables['scan'][key], equal_nan=True), f'monolith regression differs: {key}')
                compare_baseline_files(old_folder, folder)
                record['baseline_regression'] = 'exact scan columns, summary bytes, original histogram numerics, and final RNG bytes'
        else:
            require(not any(line.startswith('/run/beamOn') for line in command_lines((folder / 'run.mac').read_text())), 'geometry phase ran events')
            record['events'] = 0
        records.append(record)
    require(len(binary_hashes) == 1, 'campaign mixed executable hashes')
    if executable:
        require(binary_hashes == {digest(executable)}, 'executable changed after running')
    return {'schema_version': SCHEMA, 'status': 'passed', 'phase': m['phase'],
            'readout_mode': m['configuration']['readout_mode'],
            'manifest_sha256': manifest_hash, 'executable_sha256': next(iter(binary_hashes)),
            'source_files_sha256': m['source_files_sha256'],
            'scope': 'Frozen bounded study checks; not full optical terminal closure or physical calibration.', 'tasks': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True, type=Path)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--executable', type=Path)
    parser.add_argument('--baseline', type=Path, help='Override historical baseline location after relocation')
    parser.add_argument('--report', type=Path, help='Fresh output report path; default prints JSON only')
    args = parser.parse_args()
    result = audit(args.campaign, args.repo, args.executable, args.baseline)
    if args.report:
        write_json(args.report, result)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
