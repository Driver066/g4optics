"""Regressions from the three real v1 reflected-photon false detections."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probes
from audit import COUNT_FIELDS, EDEP_COMPONENTS, audit_readout


def seam_fixture(sensor: int, reflected_event: int | None = None,
                 false_detection: bool = False, later_entry: bool = False) -> tuple[dict, dict]:
    """A 10-primary seam task with the exact observed reflection/recovery pair.

    v1 sensor0 events 0/4: (2,3,3,1), (3,2,13,1), yet D=1.
    v1 sensor1 event 5:    (2,4,3,1), (4,2,13,1), yet D=1.
    These are aggregate boundary rows, not fabricated first-hit probabilities.
    """
    probe = probes.specifications(0.0, 0.1)[7 + sensor]
    n = probe['events']
    zeros = lambda: np.zeros(n)
    a = {key: zeros() for key in (*COUNT_FIELDS, *EDEP_COMPONENTS,
                                  'tile_edep_mev', 'steel_edep_mev', 'hit_valid')}
    a.update(event_id=np.arange(n), primary_kinetic_energy_mev=np.full(n, 3e-6),
             collection_efficiency_valid=zeros(), collection_efficiency=np.full(n, np.nan))
    for axis, value in zip('xyz', probe['position_mm']):
        a[f'shoot_{axis}_mm'] = np.full(n, value)
    c_fields = ['front_generated', 'back_generated', 'optical_parent_births',
                'boundary_steps', 'joint_boundary_steps', 'no_rindex_steps']
    c_fields += [f'lineage_{piece}_to_sensor{i}' for piece in ('front', 'back') for i in (0, 1)]
    c_fields += [f'unknown_to_sensor{i}' for i in (0, 1)]
    c_fields += [f'all_births_{piece}' for piece in ('front', 'back', 'joint', 'other')]
    c_fields += [piece + suffix for piece in ('front', 'back', 'joint')
                 for suffix in ('_edep_mev', '_optical_edep_mev')]
    c = {key: zeros() for key in c_fields}
    c['event_id'] = np.arange(n)
    records = []
    for event in range(n):
        reflected = event == reflected_event
        records.append((event, 2, 3 + sensor, 3 if reflected else 2, 1))
        if reflected:
            records.append((event, 3 + sensor, 2, 13, 1))
            if later_entry:
                records.append((event, 2, 3 + sensor, 2, 1))
        detected = int(not reflected or false_detection or later_entry)
        a['sipm_detected_photons'][event] = detected
        a[f'sipm_sensor_{sensor}_detected_photons'][event] = detected
        c[f'unknown_to_sensor{sensor}'][event] = detected
    b = {key: np.array([row[index] for row in records], dtype=int)
         for index, key in enumerate(('event_id', 'pre_region', 'post_region', 'status', 'count'))}
    c['boundary_steps'] = np.bincount(b['event_id'], weights=b['count'], minlength=n)
    c['joint_boundary_steps'] = c['boundary_steps'].copy()
    r = {'event_id': np.arange(n), 'no_rindex_steps': zeros()}
    for i in (0, 1):
        for suffix, mask in (
            ('entries', (b['post_region'] == 3 + i) & np.isin(b['status'], [1, 2, 12])),
            ('reflections', (b['post_region'] == 3 + i) & np.isin(b['status'], [3, 4])),
            ('step_too_small', (b['pre_region'] == 3 + i) & (b['status'] == 13)),
        ):
            r[f'sensor{i}_{suffix}'] = np.bincount(b['event_id'][mask], weights=b['count'][mask], minlength=n)
    return {'scan': a, 'combined_events': c, 'combined_boundaries': b, 'readout_events': r}, probe


class SensorEntryRegressionTests(unittest.TestCase):
    observed = ((0, 0), (0, 4), (1, 5))

    def test_all_three_observed_false_detections_are_rejected(self):
        for sensor, event in self.observed:
            with self.subTest(sensor=sensor, event=event):
                tables, probe = seam_fixture(sensor, event, false_detection=True)
                with self.assertRaisesRegex(ValueError, 'D must equal successful non-sensor boundary entries'):
                    probes.audit_tables(tables, probe)

    def test_reflection_and_recovery_alone_produce_zero_d(self):
        for sensor, event in self.observed:
            with self.subTest(sensor=sensor, event=event):
                tables, probe = seam_fixture(sensor, event)
                report = probes.audit_tables(tables, probe)
                self.assertEqual(audit_readout(tables, probe['events'])['mode'], 'boundary-entry')
                self.assertEqual(report['sensor_successful_boundary_entries'][sensor], 9)
                self.assertEqual(report['sensor_reflected_without_entry_event_ids'][sensor], [event])

    def test_independent_readout_observer_also_rejects_false_d(self):
        for sensor, event in self.observed:
            with self.subTest(sensor=sensor, event=event):
                tables, probe = seam_fixture(sensor, event, false_detection=True)
                with self.assertRaisesRegex(ValueError, 'readout true entries sensor'):
                    audit_readout(tables, probe['events'])

    def test_reflected_photon_can_later_enter_and_count(self):
        tables, probe = seam_fixture(0, 0, later_entry=True)
        report = probes.audit_tables(tables, probe)
        self.assertEqual(report['sensor_successful_boundary_entries'][0], 10)
        self.assertEqual(report['sensor_reflected_without_entry_event_ids'][0], [])

    def test_internal_sensor_step_does_not_establish_entry(self):
        tables, probe = seam_fixture(0)
        tables['combined_boundaries']['pre_region'][0] = 3
        tables['combined_events']['joint_boundary_steps'][0] = 0
        with self.assertRaisesRegex(ValueError, 'D must equal successful non-sensor boundary entries'):
            probes.audit_tables(tables, probe)

    def test_each_successful_boundary_status_is_recognized(self):
        for status in (1, 2, 12):
            with self.subTest(status=status):
                tables, probe = seam_fixture(0)
                # Retain other refractions to preserve the seam probe's target gate.
                tables['combined_boundaries']['status'][0] = status
                self.assertEqual(probes.audit_tables(tables, probe)['sensor_totals'], [10, 0])


class ProbeSourceContractTests(unittest.TestCase):
    base = '\n'.join((
        '/opnovice2/combined/enabled true',
        '/opnovice2/combined/layers 8 0.1 8 mm',
        '/opnovice2/sipm/readoutMode boundary-entry',
        '/opnovice2/boxProperty RINDEX 0.0000020 1.58 0.0000033 1.58',
        '/opnovice2/boxProperty ABSLENGTH 0.0000020 3800 0.0000033 3800',
        '/run/initialize', '/gps/particle e-', '/gps/ene/type Arb',
        '/gps/hist/point 2.28 0', '/run/beamOn 10',
    )) + '\n'

    def test_missing_or_legacy_readout_is_rejected(self):
        for text in (self.base.replace('/opnovice2/sipm/readoutMode boundary-entry\n', ''),
                     self.base.replace('readoutMode boundary-entry', 'readoutMode legacy')):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, 'explicitly select boundary-entry'):
                    probes.make_macro(text, probes.specifications(0, 0.1)[0])

    def test_fixed_inputs_remove_beta_and_retain_actual_polarization_command(self):
        specs = probes.specifications(0, 0.1)
        self.assertEqual(sum(p['events'] for p in specs if p['particle'] == 'opticalphoton'), 370)
        self.assertEqual(sum(p['events'] for p in specs if p['particle'] == 'e-'), 10)
        for probe in specs:
            with self.subTest(probe=probe['id']):
                macro = probes.make_macro(self.base, probe)
                self.assertNotIn('/gps/hist/', macro)
                self.assertNotIn('/gps/ene/type Arb', macro)
                self.assertNotIn('/gps/clear', macro)
                self.assertNotIn('/opnovice2/gun/randomDirection', macro)
                self.assertIn('/opnovice2/sipm/readoutMode boundary-entry', macro)
                if probe['particle'] == 'opticalphoton':
                    self.assertIn(f"/opnovice2/gun/optPhotonPolar {probe['polarization_angle_deg']} deg", macro)


if __name__ == '__main__':
    unittest.main()
