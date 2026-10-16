#!/usr/bin/env python3
"""Analyze only the fixed 5-case, 2-block, 100-event exploratory matrix."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import uproot

from audit import arrays
from common import COMPARISONS, SCHEMA, digest, read_json, require, write_json


def bootstrap_difference(target: list[np.ndarray], reference: list[np.ndarray], *,
                         replicates: int = 10000, seed: int = 202610107) -> dict:
    """Independent event resampling within each seed block; no pairing across cases.

    All events, including zeros, participate. Equal block sizes are not assumed.
    The interval is a pointwise percentile bootstrap, not an equivalence test.
    """
    require(replicates >= 10000, 'at least 10000 bootstrap replicates required')
    require(target and reference and all(len(x) > 0 for x in target + reference), 'empty bootstrap block')
    require(all(np.isfinite(x).all() and (x >= 0).all() for x in target + reference), 'invalid response observations')
    rng = np.random.default_rng(seed)
    samples = []
    for blocks in (target, reference):
        means = np.zeros(replicates)
        total = sum(len(block) for block in blocks)
        for block in blocks:
            for start in range(0, replicates, 1000):
                stop = min(start + 1000, replicates)
                choices = rng.integers(0, len(block), size=(stop - start, len(block)))
                means[start:stop] += np.asarray(block)[choices].sum(axis=1) / total
        samples.append(means)
    target_mean = float(np.concatenate(target).mean())
    reference_mean = float(np.concatenate(reference).mean())
    deltas = samples[0] - samples[1]
    undefined = int(np.count_nonzero(samples[1] == 0))
    percent = (samples[0] / samples[1] - 1) * 100 if undefined == 0 else None
    return {'target_mean_D_per_event': target_mean, 'reference_mean_D_per_event': reference_mean,
            'difference_D_per_event': target_mean - reference_mean,
            'difference_ci95': np.quantile(deltas, [0.025, 0.975]).tolist(),
            'percent_difference': (target_mean / reference_mean - 1) * 100 if reference_mean else None,
            'percent_difference_ci95': np.quantile(percent, [0.025, 0.975]).tolist() if percent is not None else None,
            'undefined_percent_bootstrap_replicates': undefined,
            'bootstrap_replicates': replicates, 'bootstrap_seed': seed,
            'method': 'independent event bootstrap stratified by seed block; pointwise percentile 95%; no multiplicity correction'}


def write_csv(path: Path, rows: list[dict]):
    require(bool(rows), 'cannot write empty result table')
    with path.open('x', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def analyze(campaign: Path, report: Path, output: Path, replicates: int = 10000,
            bootstrap_seed: int = 202610107) -> dict:
    require(not output.exists(), 'analysis destination already exists')
    manifest, audit = read_json(campaign / 'manifest.json'), read_json(report)
    require(manifest['schema_version'] == SCHEMA and manifest['phase'] == 'explore', 'not exploratory inputs')
    require(audit['status'] == 'passed' and audit['phase'] == 'explore', 'exploration audit has not passed')
    require(manifest['configuration']['readout_mode'] == audit['readout_mode'] == 'boundary-entry',
            'scientific comparison requires corrected boundary-entry readout')
    require(audit['manifest_sha256'] == digest(campaign / 'manifest.json'), 'audit manifest is stale')
    require(len(manifest['tasks']) == 10 and manifest['execution']['total_events'] == 1000, 'not fixed 1000-beta matrix')
    require({t['task_id'] for t in audit['tasks']} == {t['id'] for t in manifest['tasks']}, 'audit task matrix mismatch')
    blocks = {case['id']: [] for case in manifest['cases']}
    event_rows, diagnostic_rows, boundary_rows, readout_rows = [], [], [], []
    for task in sorted(manifest['tasks'], key=lambda t: (t['case_id'], t['block'])):
        require(task['events'] == 100 and task['block'] in (0, 1), 'changed event/block budget')
        record = next(x for x in audit['tasks'] if x['task_id'] == task['id'])
        folder = campaign / task['id']
        for name, expected in record['files_sha256'].items():
            require(digest(folder / name) == expected, f'audited output changed: {task["id"]}/{name}')
        with uproot.open(folder / 'result.root') as root:
            a = arrays(root['scan']); c = arrays(root['combined_events']); b = arrays(root['combined_boundaries'])
            r = arrays(root['readout_events'])
        require(len(a['event_id']) == 100, 'event rows changed')
        blocks[task['case_id']].append(a['sipm_detected_photons'])
        for i in range(100):
            row = {'case_id': task['case_id'], 'block': task['block'], 'event_id': int(a['event_id'][i])}
            for key in ('primary_kinetic_energy_mev', 'generated_optical_photons', 'scintillation_photons',
                        'sipm_detected_photons', 'sipm_sensor_0_detected_photons', 'sipm_sensor_1_detected_photons',
                        'tile_edep_mev', 'hit_valid'):
                row[key] = a[key][i].item()
            event_rows.append(row)
        for i in range(len(r['event_id'])):
            readout_rows.append({'case_id': task['case_id'], 'block': task['block'],
                                 **{key: values[i].item() for key, values in r.items()}})
        for i in range(len(c['event_id'])):
            row = {'case_id': task['case_id'], 'block': task['block']}
            row.update({key: values[i].item() for key, values in c.items()})
            for piece in ('front', 'back', 'joint'):
                row[f'{piece}_nonoptical_edep_mev'] = row[f'{piece}_edep_mev'] - row[f'{piece}_optical_edep_mev']
            diagnostic_rows.append(row)
        for i in range(len(b['event_id'])):
            boundary_rows.append({'case_id': task['case_id'], 'block': task['block'],
                                  **{key: values[i].item() for key, values in b.items()}})
    require(all(len(case_blocks) == 2 for case_blocks in blocks.values()), 'incomplete seed blocks')
    summaries = []
    for case_id, case_blocks in blocks.items():
        d = np.concatenate(case_blocks)
        summaries.append({'case_id': case_id, 'events': len(d), 'D_sum': int(d.sum()),
                          'D_per_event': float(d.mean()), 'D_per_event_sample_SE': float(d.std(ddof=1) / np.sqrt(len(d))),
                          'zero_D_events': int((d == 0).sum()),
                          'block0_D_per_event': float(case_blocks[0].mean()), 'block1_D_per_event': float(case_blocks[1].mean())})
    comparisons = []
    for index, (target, reference) in enumerate(COMPARISONS):
        comparisons.append({'target': target, 'reference': reference,
                            'kind': 'orientation exploration' if index == 3 else 'joined versus monolith',
                            **bootstrap_difference(blocks[target], blocks[reference], replicates=replicates,
                                                   seed=bootstrap_seed + index)})
    result = {'schema_version': SCHEMA, 'scope': 'fixed 1000-beta exploratory comparison; no equivalence conclusion',
              'manifest_sha256': digest(campaign / 'manifest.json'), 'audit_sha256': digest(report),
              'readout_mode': 'boundary-entry',
              'primary_outcome': 'corrected ideal SiPM boundary-entry arrivals D per source beta event, including non-entering and zero-response events',
              'inference': 'Pointwise independent-event percentile bootstrap within each seed block; intervals are not corrected for four comparisons.',
              'limitations': ['Only 200 events per configuration in two seed blocks; no adaptive extension.',
                             'The study geometry, bare polished outer surfaces and point source are simplified fixed conditions, not lab calibration.',
                             'Legacy generated count excludes optical-parent births; D/G is diagnostic, not a complete optical efficiency.',
                             'Legacy tile edep includes optical energy deposition; separate nonoptical diagnostic is retained.',
                             'Boundary post_region identifies candidate neighbor; reflection does not establish crossing.',
                             'Effective EJ-550 attenuation from sample transmission does not separate interface/scattering losses.'],
              'summaries': summaries, 'comparisons': comparisons}
    output.mkdir(parents=True)
    write_json(output / 'analysis.json', result)
    write_csv(output / 'events.csv', event_rows)
    write_csv(output / 'summary.csv', summaries)
    write_csv(output / 'readout_events.csv', readout_rows)
    if diagnostic_rows:
        write_csv(output / 'combined_events.csv', diagnostic_rows)
    if boundary_rows:
        write_csv(output / 'combined_boundaries.csv', boundary_rows)
    comparison_rows = []
    for row in comparisons:
        comparison_rows.append({key: value for key, value in row.items() if key not in ('difference_ci95', 'percent_difference_ci95')} |
            {'difference_ci95_low': row['difference_ci95'][0], 'difference_ci95_high': row['difference_ci95'][1],
             'percent_ci95_low': row['percent_difference_ci95'][0] if row['percent_difference_ci95'] else None,
             'percent_ci95_high': row['percent_difference_ci95'][1] if row['percent_difference_ci95'] else None})
    write_csv(output / 'comparisons.csv', comparison_rows)
    text = ['# Finite combined-tile exploration', '', result['scope'] + '.', '', result['primary_outcome'] + '.', '',
            '| Case | Events | D/event | Zero-response events |', '|---|---:|---:|---:|']
    for row in summaries:
        text.append(f"| {row['case_id']} | {row['events']} | {row['D_per_event']:.4g} | {row['zero_D_events']} |")
    text += ['', '| Target − reference | Difference D/event [95% CI] | Relative difference [95% CI] |', '|---|---:|---:|']
    for row in comparisons:
        low, high = row['difference_ci95']
        percent = 'undefined'
        if row['percent_difference_ci95'] is not None:
            pl, ph = row['percent_difference_ci95']
            percent = f"{row['percent_difference']:.3g}% [{pl:.3g}, {ph:.3g}]"
        text.append(f"| {row['target']} − {row['reference']} | {row['difference_D_per_event']:.4g} [{low:.4g}, {high:.4g}] | {percent} |")
    text += ['', result['inference'], '', *[f'- {line}' for line in result['limitations']], '']
    (output / 'report.md').write_text('\n'.join(text))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True, type=Path)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--bootstrap-replicates', type=int, default=10000)
    parser.add_argument('--bootstrap-seed', type=int, default=202610107)
    args = parser.parse_args()
    result = analyze(args.campaign, args.audit, args.output, args.bootstrap_replicates, args.bootstrap_seed)
    print(f"Analyzed {sum(row['events'] for row in result['summaries'])} events; {len(result['comparisons'])} pointwise contrasts; {args.output}")


if __name__ == '__main__':
    main()
