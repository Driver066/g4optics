"""Shared contracts for the bounded combined-tile study; no simulation side effects."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

BASELINE_COMMIT = '404a11bcff4a2c3a4b7a83de8357cab3e37d1cb5'
SOURCE_DIRECTORY = Path('test/OpNovice2')
SPECTRUM = SOURCE_DIRECTORY / 'spectra/sr90_allowed_beta_v1_gps.mac'
TRANSMISSION = SOURCE_DIRECTORY / 'optical_data/ej550_transmission_empirical.csv'
SCHEMA = 'combined-tiles-v2'
CASES = (
    {'id': 'mono16', 'front_mm': 16.0, 'gap_mm': 0.0, 'back_mm': 0.0},
    {'id': 'mono24', 'front_mm': 24.0, 'gap_mm': 0.0, 'back_mm': 0.0},
    {'id': 'joined8-8', 'front_mm': 8.0, 'gap_mm': 0.1, 'back_mm': 8.0},
    {'id': 'joined8-16', 'front_mm': 8.0, 'gap_mm': 0.1, 'back_mm': 16.0},
    {'id': 'joined16-8', 'front_mm': 16.0, 'gap_mm': 0.1, 'back_mm': 8.0},
)
COMPARISONS = (
    ('joined8-8', 'mono16'), ('joined8-16', 'mono24'),
    ('joined16-8', 'mono24'), ('joined16-8', 'joined8-16'),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value):
    # An existing report is evidence, never an implicit retry target.
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')


def read_json(path: Path):
    return json.loads(path.read_text())


def command_lines(text: str) -> list[str]:
    return [line.partition('#')[0].strip() for line in text.splitlines()
            if line.partition('#')[0].strip().startswith('/')]


def case_geometry(case: dict) -> dict:
    front, gap, back = (float(case[key]) for key in ('front_mm', 'gap_mm', 'back_mm'))
    envelope = front + gap + back
    return {**case, 'combined': gap > 0, 'active_thickness_mm': front + back,
            'envelope_thickness_mm': envelope, 'source_z_mm': envelope / 2 + 1.5,
            'front_center_z_mm': envelope / 2 - front / 2,
            'joint_center_z_mm': (back - front) / 2 if gap else None,
            'back_center_z_mm': -envelope / 2 + back / 2 if back else None}


def absorption_vector(path: Path, reference_mm: float = 0.1) -> str:
    """Same conversion/format as run_sipm_cavity_scan.sh's isolated Python helper.

    These are *effective* attenuation lengths from measured sample transmission,
    not an independently measured pure bulk absorption coefficient.
    """
    require(math.isfinite(reference_mm) and reference_mm > 0, 'reference thickness must be positive')
    points = []
    seen = set()
    with path.open(newline='', encoding='utf-8') as handle:
        reader = csv.DictReader(row for row in handle if not row.lstrip().startswith('#'))
        require(reader.fieldnames is not None and {'wavelength_nm', 'transmission'} <= set(reader.fieldnames),
                'transmission CSV is missing columns')
        for row in reader:
            w, t = (row.get('wavelength_nm') or '').strip(), (row.get('transmission') or '').strip()
            if not w and not t:
                continue
            wavelength, transmission = float(w), float(t)
            require(math.isfinite(wavelength) and wavelength > 0, 'wavelength must be positive and finite')
            require(wavelength not in seen, 'duplicate wavelength')
            require(math.isfinite(transmission) and 0 < transmission < 1, 'transmission must be strictly between 0 and 1')
            seen.add(wavelength)
            points.append((1.239841984e-3 / wavelength, -reference_mm / math.log(transmission)))
    require(len(points) >= 2, 'expected at least two transmission rows')
    parts = ['ABSLENGTH']
    for energy, length in sorted(points):
        parts.extend((f'{energy:.10g}', f'{length:.10g}'))
    return ' '.join(parts)


def expected_seeds(case: dict, phase: str, block: int) -> list[int]:
    index = next(i for i, entry in enumerate(CASES) if entry['id'] == case['id'])
    if phase in ('geometry', 'smoke'):
        # Monolith smoke pairs intentionally match the frozen original baseline.
        return [20261010, 7000 + int(case['active_thickness_mm']) if index < 2 else 7100 + index]
    # Unique process streams; the study does not claim a paired/common-random-number comparison.
    return [26101001 + index * 101 + block * 1009, 86101001 + index * 103 + block * 1013]
