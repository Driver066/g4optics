"""Frozen contracts for one assembly, four layouts, five geometries, two readouts."""
from __future__ import annotations
import csv
from collections import OrderedDict
import hashlib
import importlib.util
import json
import math
from pathlib import Path

SCHEMA = 'combined-layouts-v1'
BASE_COMMIT = '404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5'
SOURCE = Path('test/OpNovice2')
SPECTRUM = SOURCE / 'spectra/sr90_allowed_beta_v1_gps.mac'
TRANSMISSION = SOURCE / 'optical_data/ej550_transmission_empirical.csv'
MODES = ('legacy', 'boundary-entry')
LAYOUTS = {
    'side-two': {'detector_layout': 'edge-two', 'face': '+X', 'local_centers': [[-25, 0], [25, 0]]},
    'back-center': {'detector_layout': 'back-center', 'face': '-Z', 'local_centers': [[0, 0]]},
    'back-two': {'detector_layout': 'back-two', 'face': '-Z', 'local_centers': [[-25, -25], [25, 25]]},
    'back-four': {'detector_layout': 'back-four', 'face': '-Z', 'local_centers': [[-25, -25], [-25, 25], [25, -25], [25, 25]]},
}
GEOMETRIES = (
    {'id': 'mono16', 'front_mm': 16., 'gap_mm': 0., 'back_mm': 0.},
    {'id': 'mono24', 'front_mm': 24., 'gap_mm': 0., 'back_mm': 0.},
    {'id': 'joined8-8', 'front_mm': 8., 'gap_mm': .1, 'back_mm': 8.},
    {'id': 'joined8-16', 'front_mm': 8., 'gap_mm': .1, 'back_mm': 16.},
    {'id': 'joined16-8', 'front_mm': 16., 'gap_mm': .1, 'back_mm': 8.},
)
JOIN_PAIRS = (('joined8-8', 'mono16'), ('joined8-16', 'mono24'), ('joined16-8', 'mono24'))
PHASES = {'geometry': (0, 1), 'smoke': (5, 1), 'explore': (100, 2)}
SENSOR_REGIONS = (3, 4, 8, 9)
REGIONS = {'front': 0, 'back': 1, 'joint': 2, 'sensor0': 3, 'sensor1': 4,
           'world': 5, 'other': 6, 'outside': 7, 'sensor2': 8, 'sensor3': 9}
CORE_TOOLS = ('study.py', 'validation.py', 'prepare.py', 'run.py', 'audit.py', 'analyze.py', 'qualify.py')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')


def safe_child(root, relative):
    root = Path(root).resolve(); child = (root / relative).resolve()
    require(root in child.parents, 'path escapes evidence directory')
    return child


def command_lines(text):
    return [s for line in text.splitlines() if (s := line.partition('#')[0].strip()).startswith('/')]


def configurations():
    output = []
    for mode in MODES:
        for layout, details in LAYOUTS.items():
            for raw in GEOMETRIES:
                f, g, b = (raw[k] for k in ('front_mm', 'gap_mm', 'back_mm'))
                envelope = f + g + b
                output.append({**raw, 'geometry': raw['id'], 'layout': layout, 'readout_mode': mode,
                    'id': f'{mode}/{layout}/{raw["id"]}', 'combined': bool(g),
                    'active_thickness_mm': f+b, 'envelope_thickness_mm': envelope,
                    'source_z_mm': envelope/2 + 1.5, 'joint_center_z_mm': (b-f)/2 if g else None,
                    'front_center_z_mm': envelope/2-f/2, 'back_center_z_mm': -envelope/2+b/2 if b else None,
                    'face': details['face'], 'detector_layout': details['detector_layout'],
                    'sensor_count': len(details['local_centers']),
                    'sensor_area_mm2': len(details['local_centers'])*2.4*2.4,
                    'local_centers': details['local_centers'],
                    'sensor_centers_mm': [[50.25,u,v] if details['face']=='+X' else [u,v,-envelope/2-.25]
                                          for u,v in details['local_centers']]})
    return output


def seed_pair(index, phase, block, geometry=False):
    require(phase in PHASES and 0 <= index < 40 and 0 <= block < PHASES[phase][1], 'invalid seed coordinates')
    base = 2026101200 + (list(PHASES).index(phase)+1)*10000 + index*10 + block*2 + (5000 if geometry else 0)
    seeds = [base+1, base+2]
    require(max(seeds) <= 2147483646, 'seed outside Geant4 signed range')
    return seeds


def registered_seeds(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('seeds', 'geometry_seeds', 'geometry_sampling_seed') and isinstance(item, list):
                found.update(v for v in item if isinstance(v, int))
            found.update(registered_seeds(item))
    elif isinstance(value, list):
        for item in value:
            found.update(registered_seeds(item))
    return found


def source_paths(repo):
    source=repo/SOURCE
    paths=[source/'CMakeLists.txt',source/'OpNovice2.cc',source/'generate_scan_macro.py',
           source/'ej200_sipm_gps_test.mac',repo/SPECTRUM,repo/TRANSMISSION]
    paths+=sorted((source/'src').glob('*.cc'))+sorted((source/'include').glob('*.hh'))
    paths+=[repo/'tools/combined_layouts'/name for name in CORE_TOOLS]
    return paths


def old_seed_registry(repo):
    files, seeds = {}, set()
    # Existing combined studies, including rejected attempts, remain reserved.
    root = repo / 'outputs/combined-tiles'
    for path in sorted(root.rglob('manifest.json')) if root.exists() else []:
        value = read_json(path)
        files[str(path.relative_to(repo))] = digest(path)
        seeds.update(registered_seeds(value))
    return {'scope': 'all prior outputs/combined-tiles/**/manifest.json including rejected attempts',
            'files_sha256': files, 'reserved_seed_values': sorted(seeds)}


def absorption_vector(path):
    points, seen = [], set()
    with Path(path).open() as handle:
        for row in csv.DictReader(line for line in handle if not line.lstrip().startswith('#')):
            w, t = float(row['wavelength_nm']), float(row['transmission'])
            require(math.isfinite(w) and w>0 and w not in seen, 'invalid or duplicate wavelength')
            require(math.isfinite(t) and 0<t<1, 'invalid transmission')
            seen.add(w); points.append((1.239841984e-3/w, -.1/math.log(t)))
    require(len(points)>1, 'insufficient transmission points')
    return 'ABSLENGTH ' + ' '.join(f'{v:.10g}' for point in sorted(points) for v in point)


def make_macro(repo, config, events, seeds, geometry_seeds):
    spec = importlib.util.spec_from_file_location('combined_layout_macro_generator', repo / SOURCE / 'generate_scan_macro.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    template = (repo / SOURCE / 'ej200_sipm_gps_test.mac').read_text().splitlines(keepends=True)
    settings = OrderedDict([
        ('/opnovice2/tank/size', f'100 100 {config["active_thickness_mm"]:g} mm'),
        ('/opnovice2/tank/bottomCavity', 'false'), ('/opnovice2/absorber/enabled', 'false'),
        ('/opnovice2/stack/enabled', 'false'), ('/opnovice2/dimple/enabled', 'false'),
        ('/opnovice2/grease/enabled', 'false'), ('/opnovice2/combined/layoutStudy', 'true'),
        ('/opnovice2/combined/enabled', 'true' if config['combined'] else 'false'),
        ('/opnovice2/sipm/readoutMode', config['readout_mode']),
        ('/opnovice2/sipm/layout', config['detector_layout']), ('/opnovice2/sipm/face', config['face']),
        ('/opnovice2/sipm/localPosition', '0 0 0 mm'), ('/opnovice2/sipm/size', '2.4 2.4 0.5 mm'),
        ('/gps/pos/centre', f'0 0 {config["source_z_mm"]:g} mm'),
        ('/analysis/setFileName', 'result'), ('/control/verbose', '1')])
    if config['combined']:
        settings['/opnovice2/combined/layers'] = f'{config["front_mm"]:g} {config["gap_mm"]:g} {config["back_mm"]:g} mm'
    text = ''.join(module.generate_macro(template, settings, set(settings), removed_commands={
        '/run/beamOn','/gps/energy','/run/numberOfThreads','/opnovice2/gun/electronEnergyMode'}))
    if config['combined']:
        props = '/opnovice2/greaseProperty RINDEX 0.0000020 1.46 0.0000033 1.46\n/opnovice2/greaseProperty '+absorption_vector(repo/TRANSMISSION)+'\n'
        require(text.count('/run/initialize\n')==1, 'initialize not unique')
        text = text.replace('/run/initialize\n', props+'/run/initialize\n')
    spectrum = (repo/SPECTRUM).read_text()
    text += '\n'+spectrum+'\n'
    text += f'/random/setSeeds {geometry_seeds[0]} {geometry_seeds[1]}\n/geometry/test/resolution 10000\n/geometry/test/run\n'
    text += f'/random/setSeeds {seeds[0]} {seeds[1]}\n'
    if events:
        text += f'/random/setSavingFlag true\n/random/setDirectoryName ./\n/run/beamOn {events}\n'
    require(text.count(spectrum)==1, 'beta spectrum not copied exactly once')
    return text
