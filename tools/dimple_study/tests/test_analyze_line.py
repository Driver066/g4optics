"""Small mathematical controls for whole-event resampling and ratio semantics."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

spec=importlib.util.spec_from_file_location('analyze_line',Path(__file__).resolve().parents[1]/'analyze_line.py')
analysis=importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class AnalysisTests(unittest.TestCase):
    def test_ratio_of_totals_is_not_mean_event_ratio(self):
        values=np.array([[1,1,0,1,0,0],[9,99,0,99,0,0]],dtype=float)
        actual=analysis.derived(values.mean(axis=0))['legacy_D_over_G']
        self.assertAlmostEqual(float(actual),.1)
        self.assertNotAlmostEqual(float(actual),float(np.mean(values[:,0]/values[:,1])))

    def test_joint_event_weights_preserve_exact_linear_relationship(self):
        values=np.array([[2,1,0,1,0,0],[20,10,0,10,0,0],[200,100,0,100,0,0]],dtype=float)
        means=analysis.bootstrap_means(values,np.random.default_rng(9327),2000)
        ratios=analysis.derived(means)['legacy_D_over_G']
        np.testing.assert_array_equal(ratios,np.full(2000,2.))

    def test_zero_denominator_is_retained_as_undefined(self):
        values=np.zeros((3,6))
        value=analysis.derived(values.mean(axis=0))['legacy_D_over_G']
        result=analysis.interval(value,np.array([.1,np.nan,.2]))
        self.assertIsNone(result['estimate'])
        self.assertIsNone(result['lo95'])
        self.assertIsNone(result['hi95'])
        self.assertEqual(result['invalid_bootstrap'],1)

    def test_adjusted_interval_uses_thirteen_comparisons(self):
        samples=np.arange(100001,dtype=float)
        result=analysis.interval(50000,samples,adjusted=True)
        self.assertAlmostEqual(result['lo_adjusted'],100000*.05/26)
        self.assertAlmostEqual(result['hi_adjusted'],100000*(1-.05/26))


if __name__=='__main__':
    unittest.main()
