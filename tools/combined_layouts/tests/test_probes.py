"""Controlled sensor probes: 1/2/4 sensors, noncontiguous region IDs, two readout meanings."""
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probes
from study import PHASES, SENSOR_REGIONS, command_lines, configurations, make_macro, seed_pair


def fixture(mode='boundary-entry', layout='back-four', sensor=2, reflect=False, false_tally=False):
    probe = next(p for p in probes.specifications() if p['mode'] == mode and p['layout'] == layout
                 and p['sensor'] == sensor and p['kind'] == 'plastic')
    config = next(c for c in configurations() if c['id'] == probe['configuration_id'])
    n = probe['events']; zero = lambda: np.zeros(n)
    a = {name: zero() for name in ('generated_optical_photons', 'scintillation_photons', 'cerenkov_photons',
                                    'sipm_detected_photons', 'tile_edep_mev', 'collection_efficiency_valid')}
    a.update({f'sipm_sensor_{i}_detected_photons': zero() for i in range(4)})
    a.update(event_id=np.arange(n), primary_kinetic_energy_mev=np.full(n, 3e-6), collection_efficiency=np.full(n, np.nan))
    for axis, coordinate in zip('xyz', probe['position_mm']):a[f'shoot_{axis}_mm'] = np.full(n, coordinate)
    fields = ['front_generated','back_generated','optical_parent_births','boundary_steps','joint_boundary_steps','no_rindex_steps']
    fields += [f'lineage_{piece}_to_sensor{i}' for piece in ('front','back') for i in range(4)]
    fields += [f'unknown_to_sensor{i}' for i in range(4)]
    fields += [f'all_births_{piece}' for piece in ('front','back','joint','other')]
    fields += [piece+suffix for piece in ('front','back','joint') for suffix in ('_edep_mev','_optical_edep_mev')]
    c = {name:zero() for name in fields};c['event_id'] = np.arange(n)
    r = {f'sensor{i}_{suffix}':zero() for i in range(4) for suffix in ('entries','reflections','step_too_small')}
    r.update(event_id=np.arange(n),no_rindex_steps=zero())
    rows = []
    piece = 'front' if probe['source_region'] == 0 else 'back'
    for event in range(n):
        reflected = reflect and event == 0
        rows.append((event,probe['source_region'],SENSOR_REGIONS[sensor],3 if reflected else 2,1))
        if reflected:rows.append((event,SENSOR_REGIONS[sensor],probe['source_region'],13,1))
        actual = int(not reflected)
        tally = int(not reflected or mode == 'legacy' or false_tally)
        a['sipm_detected_photons'][event] = tally
        a[f'sipm_sensor_{sensor}_detected_photons'][event] = tally
        c[f'lineage_{piece}_to_sensor{sensor}'][event] = tally
        r[f'sensor{sensor}_entries'][event] = actual
        r[f'sensor{sensor}_reflections'][event] = int(reflected)
    b = {name:np.array([row[i] for row in rows],dtype=int)
         for i,name in enumerate(('event_id','pre_region','post_region','status','count'))}
    c['boundary_steps'] = np.bincount(b['event_id'],weights=b['count'],minlength=n)
    return {'scan':a,'combined_events':c,'combined_boundaries':b,'readout_events':r},config,probe


class SensorMeaningTests(unittest.TestCase):
    def test_every_active_sensor_in_both_modes_is_audited(self):
        for mode in ('legacy','boundary-entry'):
            for layout,count in (('side-two',2),('back-center',1),('back-two',2),('back-four',4)):
                for sensor in range(count):
                    with self.subTest(mode=mode,layout=layout,sensor=sensor):
                        tables,config,probe=fixture(mode,layout,sensor)
                        result=probes.audit_tables(tables,config,probe)
                        self.assertEqual(result['sensor_true_entries'][sensor],50)

    def test_legacy_known_false_tally_is_reported_not_presented_as_entry(self):
        tables,config,probe=fixture('legacy',reflect=True)
        result=probes.audit_tables(tables,config,probe)
        self.assertEqual(result['sensor_D_tallies'][2],50)
        self.assertEqual(result['sensor_true_entries'][2],49)
        self.assertEqual(result['legacy_tally_excess_over_own_true_entries'][2],1)

    def test_corrected_false_tally_is_rejected_for_sensor_two_and_three(self):
        for sensor in (2,3):
            with self.subTest(sensor=sensor):
                tables,config,probe=fixture(sensor=sensor,reflect=True,false_tally=True)
                with self.assertRaisesRegex(ValueError,'corrected D differs'):
                    probes.audit_tables(tables,config,probe)

    def test_corrected_reflection_is_not_a_detection(self):
        tables,config,probe=fixture(sensor=3,reflect=True)
        result=probes.audit_tables(tables,config,probe)
        self.assertEqual(result['sensor_true_entries'][3],49)
        self.assertEqual(result['sensor_D_tallies'][3],49)
        self.assertEqual(result['reflected_without_later_sensor_entry_event_ids'][3],[0])

    def test_world_region_five_is_never_sensor_two(self):
        tables,config,probe=fixture(sensor=2)
        tables['combined_boundaries']['post_region'][0]=5
        with self.assertRaisesRegex(ValueError,'passive true entry sensor 2'):
            probes.audit_tables(tables,config,probe)

    def test_legacy_also_requires_passive_true_entry_evidence(self):
        tables,config,probe=fixture('legacy',reflect=True)
        tables['readout_events']['sensor2_entries'][0]=1
        with self.assertRaisesRegex(ValueError,'passive true entry sensor 2'):
            probes.audit_tables(tables,config,probe)


class SourceMatrixTests(unittest.TestCase):
    def test_budget_and_independent_streams_cover_all_modes_and_sensors(self):
        specs=probes.specifications()
        self.assertEqual(len(specs),22)
        self.assertEqual(sum(p['events'] for p in specs),1100)
        self.assertEqual({p['geometry'] for p in specs},{'joined8-8'})
        values=[value for p in specs for value in p['seeds']+p['geometry_seeds']]
        self.assertEqual(len(values),len(set(values)))
        planned=set()
        for i in range(40):
            for phase,(_,blocks) in PHASES.items():
                for block in range(blocks):
                    for geometry in (False,True):planned.update(seed_pair(i,phase,block,geometry))
        self.assertFalse(set(values)&planned)

    def test_live_smoke_macros_yield_controlled_polarized_rays(self):
        repo=Path(__file__).resolve().parents[3]
        configs={c['id']:c for c in configurations()}
        for probe in probes.specifications():
            with self.subTest(probe=probe['id']):
                config=configs[probe['configuration_id']]
                base=make_macro(repo,config,5,[1001,1002],[2001,2002])
                lines=command_lines(probes.make_macro(base,config,probe))
                self.assertNotIn('/gps/particle e-',lines)
                self.assertFalse(any(line.startswith(('/gps/hist/','/gps/clear','/gps/source/','/opnovice2/gun/randomDirection')) for line in lines))
                self.assertIn('/gps/ene/mono 3 eV',lines)
                self.assertIn(f'/opnovice2/gun/optPhotonPolar {probe["polarization_angle_deg"]} deg',lines)
                k=np.array(probe['direction']);cross=np.cross([1,0,0],k)
                ep=cross/np.linalg.norm(cross) if np.linalg.norm(cross)>0 else np.array([0,0,1])
                theta=np.deg2rad(probe['polarization_angle_deg'])
                np.testing.assert_allclose(np.cos(theta)*np.cross(ep,k)+np.sin(theta)*ep,[0,1,0],atol=1e-14)
                if probe['kind']=='plastic' and config['face']=='+X':self.assertGreater(probe['position_mm'][2],.05)
                if config['face']=='-Z':self.assertAlmostEqual(probe['position_mm'][2],-8.04)


if __name__=='__main__':unittest.main()
