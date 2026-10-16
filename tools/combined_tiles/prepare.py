#!/usr/bin/env python3
"""Prepare fixed geometry, smoke, or 1000-beta exploration inputs. Never run Geant4."""
from __future__ import annotations

import argparse
from collections import OrderedDict
import importlib.util
from pathlib import Path
import subprocess

from common import (BASELINE_COMMIT, CASES, SCHEMA, SOURCE_DIRECTORY, SPECTRUM, TRANSMISSION,
                    absorption_vector, case_geometry, command_lines, digest,
                    expected_seeds, require, write_json)


def make_macro(repo: Path, case: dict, events: int, seeds: list[int], readout_mode: str = 'boundary-entry') -> str:
    require(readout_mode in ('boundary-entry', 'legacy'), 'invalid readout mode')
    spec = importlib.util.spec_from_file_location('combined_macro_generator', repo / SOURCE_DIRECTORY / 'generate_scan_macro.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    template = (repo / SOURCE_DIRECTORY / 'ej200_sipm_gps_test.mac').read_text().splitlines(keepends=True)
    settings = OrderedDict([
        ('/opnovice2/tank/size', f"100 100 {case['active_thickness_mm']:g} mm"),
        ('/opnovice2/tank/bottomCavity', 'false'),
        ('/opnovice2/absorber/enabled', 'false'), ('/opnovice2/stack/enabled', 'false'),
        ('/opnovice2/dimple/enabled', 'false'), ('/opnovice2/grease/enabled', 'false'),
        ('/opnovice2/combined/enabled', 'true' if case['combined'] else 'false'),
        ('/opnovice2/sipm/readoutMode', readout_mode),
        ('/opnovice2/sipm/layout', 'edge-two'), ('/opnovice2/sipm/face', '+X'),
        ('/opnovice2/sipm/size', '2.4 2.4 0.5 mm'),
        ('/gps/pos/centre', f"0 0 {case['source_z_mm']:g} mm"),
        ('/analysis/setFileName', 'result'), ('/control/verbose', '1'),
    ])
    if case['combined']:
        settings['/opnovice2/combined/layers'] = f"{case['front_mm']:g} {case['gap_mm']:g} {case['back_mm']:g} mm"
    text = ''.join(module.generate_macro(template, settings, set(settings), removed_commands={
        '/run/beamOn', '/gps/energy', '/run/numberOfThreads', '/opnovice2/gun/electronEnergyMode'}))
    if case['combined']:
        properties = ('/opnovice2/greaseProperty RINDEX 0.0000020 1.46 0.0000033 1.46\n'
                      '/opnovice2/greaseProperty ' + absorption_vector(repo / TRANSMISSION) + '\n')
        require(text.count('/run/initialize\n') == 1, 'template initialize must be unique')
        text = text.replace('/run/initialize\n', properties + '/run/initialize\n')
    spectrum = (repo / SPECTRUM).read_text()
    text += '\n' + spectrum + '\n'
    text += '/random/setSeeds 171007 271007\n/geometry/test/resolution 10000\n/geometry/test/run\n'
    text += f'/random/setSeeds {seeds[0]} {seeds[1]}\n'
    if events:
        text += f'/random/setSavingFlag true\n/random/setDirectoryName ./\n/run/beamOn {events}\n'
    require(text.count(spectrum) == 1, 'source spectrum was not copied exactly once')
    require(not any(line.startswith('/gps/energy ') for line in command_lines(text)), 'fixed energy override')
    return text


def prepare(repo: Path, output: Path, phase: str, baseline: Path | None = None,
            readout_mode: str = 'boundary-entry') -> dict:
    repo, output = repo.resolve(), output.resolve()
    require(phase in ('geometry', 'smoke', 'explore'), 'invalid phase')
    require(readout_mode in ('boundary-entry', 'legacy'), 'invalid readout mode')
    require(phase != 'explore' or readout_mode == 'boundary-entry', 'exploration requires boundary-entry readout')
    require(baseline is None or readout_mode == 'legacy', 'historical baseline regression requires legacy readout')
    require(not output.exists(), f'refusing to overwrite existing output: {output}')
    count, blocks = {'geometry': (0, 1), 'smoke': (10, 1), 'explore': (100, 2)}[phase]
    cases = [case_geometry(case) for case in CASES]
    # Validate/read everything before creating the output tree.
    macros = []
    for case in cases:
        for block in range(blocks):
            seeds = expected_seeds(case, phase, block)
            macro = make_macro(repo, case, count, seeds, readout_mode)
            macros.append((case, block, seeds, macro))
    source = repo / SOURCE_DIRECTORY
    files = [source / 'CMakeLists.txt', source / 'OpNovice2.cc', source / 'generate_scan_macro.py',
             source / 'ej200_sipm_gps_test.mac', repo / SPECTRUM, repo / TRANSMISSION]
    files += sorted((source / 'src').glob('*.cc')) + sorted((source / 'include').glob('*.hh'))
    files += [repo / 'tools/combined_tiles' / name for name in ('common.py', 'prepare.py', 'run.py', 'audit.py', 'analyze.py')]
    baseline_files = {}
    if baseline is not None:
        require(phase == 'smoke', 'baseline regression applies only to smoke')
        baseline = baseline.resolve()
        for thickness in (16, 24):
            for name in ('result.root', 'result_summary.csv', 'run.mac', 'receipt.json', 'currentEvent.rndm', 'currentRun.rndm'):
                path = baseline / f'monolith-{thickness}/smoke' / name
                baseline_files[str(path.relative_to(baseline))] = digest(path)
    manifest = {
        'schema_version': SCHEMA, 'phase': phase,
        'scope': 'engineering validation' if phase != 'explore' else 'fixed finite exploratory comparison; no equivalence claim',
        'base_commit': BASELINE_COMMIT,
        'working_head': subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
        'source_root_at_preparation': str(repo),
        'source_files_sha256': {str(path.relative_to(repo)): digest(path) for path in files},
        'source_spectrum': {'model': 'sr90_allowed_beta_v1', 'file': str(SPECTRUM), 'sha256': digest(repo / SPECTRUM),
                            'method': 'original GPS arbitrary histogram copied byte-for-byte; not fixed energy'},
        'optical_joint': {'material': 'EJ-550', 'rindex': 1.46, 'thickness_mm': 0.1,
            'absorption_model': 'ej550-transmission-derived-effective', 'formula': 'L_mm = -0.1 / log(T)',
            'transmission_file': str(TRANSMISSION), 'sha256': digest(repo / TRANSMISSION),
            'reference_thickness_mm': 0.1, 'data_wavelength_range_nm': [280, 500],
            'properties': absorption_vector(repo / TRANSMISSION),
            'limitations': ['Digitized approximate manufacturer sample transmission, not pure bulk absorption.',
                           'Interface reflection and scattering are not separated; explicit Fresnel boundaries may double-count part of the loss.',
                           'The actual 2.4982-3.1001 eV scintillation support lies entirely inside the 280-500 nm transmission data; no emission extrapolation is required. RINDEX is explicitly padded over 2.0-3.3 eV.']},
        'configuration': {'readout_mode': readout_mode, 'lateral_mm': [100, 100], 'surface': 'bare polished', 'surface_sigma_alpha': 0.0,
            'sensor_layout': 'edge-two', 'sensor_size_mm': [2.4, 2.4, 0.5],
            'sensor_centers_mm': [[50.25, -25, 0], [50.25, 25, 0]],
            'source': 'central point, initial direction -Z, 1.5 mm above physical envelope entrance',
            'front_definition': '+Z piece faces source', 'sipm_coupling': 'existing zero-gap proxy',
            'study_assumptions': ['100x100 mm and stated SiPM dimensions fixed for this study', 'bare polished outer surface', 'point source with zero initial divergence', 'laboratory calibration not established'],
            'generated_definition': 'existing scintillation plus Cerenkov generation; not complete optical-history accounting',
            'detection_definition': ('true boundary-entry into ideal SiPM proxy; transmission/refraction/same-material status, without PDE'
                                     if readout_mode == 'boundary-entry' else
                                     'legacy pre-step SiPM volume tally; known reflection miscount, regression only')},
        'execution': {'events_per_task': count, 'blocks_per_case': blocks, 'total_events': count * blocks * len(cases),
            'one_process_per_task': True, 'run_manager': 'Serial', 'retry_policy': 'none; any rerun requires a fresh output tree',
            'seeds': 'independent process streams; monolith smoke repeats frozen baseline seeds',
            'geometry_sampling_seed': [171007, 271007], 'reset_seeds_after_geometry': True},
        'baseline_regression': {'path': str(baseline), 'files_sha256': baseline_files} if baseline is not None else None,
        'cases': cases, 'tasks': [],
    }
    output.mkdir(parents=True)
    for case, block, seeds, macro in macros:
        task_id = f"{case['id']}/block-{block:02d}"
        folder = output / task_id
        folder.mkdir(parents=True)
        (folder / 'run.mac').write_text(macro)
        manifest['tasks'].append({'id': task_id, 'case_id': case['id'], 'block': block, 'events': count,
                                  'seeds': seeds, 'macro_sha256': digest(folder / 'run.mac')})
    write_json(output / 'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--phase', required=True, choices=('geometry', 'smoke', 'explore'))
    parser.add_argument('--baseline', type=Path, help='Frozen 20261010-baseline-v1 directory; legacy smoke regression only')
    parser.add_argument('--readout-mode', choices=('boundary-entry', 'legacy'), default='boundary-entry')
    args = parser.parse_args()
    manifest = prepare(args.repo, args.output, args.phase, args.baseline, args.readout_mode)
    print(f"Prepared {len(manifest['tasks'])} processes; {manifest['execution']['total_events']} events; {args.output.resolve()}")


if __name__ == '__main__':
    main()
