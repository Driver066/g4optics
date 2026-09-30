"""Scientific-design contracts: neutron grain, independent samples, and strict failures."""
import copy
import hashlib
import json
from pathlib import Path
from statistics import NormalDist
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze
import osc
import precision
import precision_io as io
import precision_math as stats
import precision_worker as worker
from model import configuration_hash, make_configuration, prepare_tasks, validate_tasks


def blocks(prefix="cal", factor=1.):
    result = []
    for i in range(10):
        d = factor*np.tile([0., 1., 2., 5., 10.], 20)
        result.append(dict(task_id=f"{prefix}-{i}", block=i,
                           values=np.column_stack((d, 10*d, 3*d, .5*d, d==0))))
    return result


FIELDS = ["detected", "generated_legacy", "births_tile", "detected_tile_birth", "zero_response"]


class PrecisionMathTests(unittest.TestCase):
    def test_zero_responses_are_included_and_auxiliary_weights_are_shared(self):
        result = stats.group_statistics(blocks(), FIELDS, replicas=200)
        self.assertEqual(result["statistics"]["events"], 1000)
        self.assertEqual(result["statistics"]["mean"], 3.6)
        self.assertEqual(result["statistics"]["zero_response_fraction"], .2)
        np.testing.assert_allclose(result["bootstrap_sums"][:,1], 10*result["bootstrap_sums"][:,0])
        self.assertAlmostEqual(result["auxiliary"]["birth_ce"]["estimate"], 1/6)
        self.assertEqual(result["auxiliary"]["zero_response"]["estimate"], .2)

    def test_chunked_direct_and_reordered_bootstrap_are_identical(self):
        data = blocks()[:2]
        expected = stats.bootstrap_sums(data, replicas=513, chunk=513)
        np.testing.assert_array_equal(expected, stats.bootstrap_sums(data, replicas=513, chunk=17))
        np.testing.assert_array_equal(expected, stats.bootstrap_sums(data[::-1], replicas=513, chunk=256))
        self.assertFalse(np.array_equal(expected, stats.bootstrap_sums(blocks("independent")[:2], replicas=513)))

    def test_bonferroni_quantiles_and_variance_formula(self):
        self.assertAlmostEqual(stats.SPEC["z"], NormalDist().inv_cdf(.99375), places=9)
        self.assertEqual(stats.SPEC["corrected_quantiles"], [.00625,.99375])
        self.assertAlmostEqual(float(stats.variance_coefficient(2., 4., 3., 9.)), 4.5)
        response = stats.interval(2., np.arange(10000.), [.00625,.99375])
        self.assertAlmostEqual(response["half_width"], (9936.50625-62.49375)/2)

    def test_precision_scales_by_four_and_rounds_up(self):
        n1 = stats.required_events(10., half_width=.05)
        n2 = stats.required_events(10., half_width=.025)
        self.assertEqual(n1%100, 0)
        self.assertGreaterEqual(n1, 1.2*stats.SPEC["z"]**2*10/.05**2)
        self.assertLessEqual(abs(n2-4*n1), 300)
        self.assertEqual(stats.required_events(.000001), 1000)

    def test_pair_direction_and_conservative_margin(self):
        reference = stats.group_statistics(blocks(), FIELDS)
        candidate = stats.group_statistics(blocks("other", factor=1.10), FIELDS)
        pair = stats.compare_pair(reference, candidate, thickness=4, layout="back-center")
        self.assertAlmostEqual(pair["calibration_delta"], .1)
        self.assertEqual((pair["reference_gap_mm"],pair["candidate_gap_mm"]),(.5,1.))
        self.assertGreaterEqual(pair["A_plus"],pair["A_hat"])
        self.assertGreaterEqual(pair["conservative_events_per_arm"],pair["point_events_per_arm"])
        self.assertFalse(pair["main_precision_verified"])
        reference["bootstrap_mean"][0] = 0
        with self.assertRaisesRegex(stats.PlanningError, "drop invalid"):
            stats.compare_pair(reference, candidate, thickness=4, layout="back-center")

    def test_degenerate_illegal_and_invalid_denominators_are_not_silently_filtered(self):
        for args in ((0,1,1,1),(1,0,1,0),(1,-1,1,1),(1,1,float("nan"),1)):
            with self.subTest(args=args), self.assertRaises(stats.PlanningError):
                stats.variance_coefficient(*args)
        for data in ([0.,-1.],[1.,float("inf")]):
            with self.assertRaises(ValueError): stats.event_statistics(data)
        result = stats.interval(1., [0.,1.,np.nan], [.025,.975])
        self.assertFalse(result["valid"])
        self.assertIsNone(result["low"])
        self.assertEqual(result["invalid_resamples"],1)

    def test_calibration_cannot_enter_default_scientific_selection(self):
        task = dict(task_id="cal",stage="calibration",purpose=io.PURPOSE,independent_sample=True,
                    config=make_configuration("back-center",4,.5,"painted-corner-v2",16))
        with self.assertRaisesRegex(ValueError,"No eligible"):
            analyze.select_tasks([task],engineering=False)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.bundle = self.root/"bundle"
        self.bundle.mkdir()
        (self.root/"controller").mkdir()
        for name in io.CONTROLLER_FILES:
            (self.root/"controller"/name).write_text("fixture: "+name)
        io.save_new(self.root/"controller/controller.json", dict(
            files={n:osc.sha256(self.root/"controller"/n) for n in io.CONTROLLER_FILES},
            analysis_identity=io.analysis_identity()))
        io.save_new(self.root/"statistics-spec.json",stats.SPEC)
        io.save_new(self.root/"excluded-benchmark-seeds.json",[7001,7002])
        (self.root/"prior-evidence").mkdir()
        io.save_new(self.root/"prior-evidence/SHA256.json",{})
        self.configs = [make_configuration(l,t,g,"painted-corner-v2",16)
                       for t,l in stats.SPEC["pairs"] for g in (.5,1.)]
        self.tasks = prepare_tasks(self.configs,events_per_task=100,blocks=10,
            campaign_seed=2026093002,total_event_budget=8000,stage="calibration",excluded_seeds=[7001,7002])
        for t in self.tasks:t["purpose"]=io.PURPOSE
        (self.bundle/"array_task.py").write_text("never executed fixture")
        self.manifest = dict(purpose="calibration",mock=False,optical_numerics={"profile":"painted-corner-v2","scale":16},
            precision_spec=stats.SPEC,remote_output_root=str(self.root/"results"),remote_bundle_root=str(self.bundle),
            summary=validate_tasks(self.tasks),worker_sha256=osc.sha256(self.bundle/"array_task.py"),
            calibration_control=dict(root=str(self.root/"controller"),
                sha256=osc.sha256(self.root/"controller/controller.json"),stop_file=str(self.root/"STOP.json"),
                spec_sha256=osc.sha256(self.root/"statistics-spec.json"),
                excluded_seeds_sha256=osc.sha256(self.root/"excluded-benchmark-seeds.json"),
                prior_evidence_sha256=osc.sha256(self.root/"prior-evidence/SHA256.json")))
        self.store()

    def store(self):
        (self.bundle/"tasks.json").write_text(json.dumps(self.tasks))
        self.manifest["tasks_sha256"]=osc.sha256(self.bundle/"tasks.json")
        (self.bundle/"manifest.json").write_text(json.dumps(self.manifest))
        self.manifest_hash=osc.sha256(self.bundle/"manifest.json")

    def test_full_matrix_and_controller_are_verified(self):
        m,t=io.read_campaign(self.bundle,self.manifest_hash)
        self.assertEqual(len(t),80)
        self.assertEqual(sum(x["events"] for x in t),8000)
        (self.root/"controller/precision_math.py").write_text("changed")
        with self.assertRaisesRegex(ValueError,"inventory"):
            io.read_campaign(self.bundle,self.manifest_hash)

    def test_missing_duplicate_mixed_and_seed_registry_changes_are_rejected(self):
        original=copy.deepcopy(self.tasks)
        for transform in (lambda t:t[:-1], lambda t:t+[t[0]],
                          lambda t:[{**t[0],"stage":"benchmark"},*t[1:]],
                          lambda t:[{**t[0],"repeat_of":t[1]["task_id"]},*t[1:]]):
            self.tasks=transform(copy.deepcopy(original));self.store()
            with self.assertRaises(ValueError):io.read_campaign(self.bundle,self.manifest_hash)
        self.tasks=original;self.store()
        (self.root/"excluded-benchmark-seeds.json").write_text("[]")
        with self.assertRaisesRegex(ValueError,"Excluded seed"):
            io.read_campaign(self.bundle,self.manifest_hash)

    def test_partial_results_cannot_generate_an_index(self):
        (self.root/"results").mkdir()
        with self.assertRaisesRegex(ValueError,"Missing/unregistered"):
            io.accepted_index(self.bundle,self.manifest_hash)

    def test_stop_preserves_first_failure_and_prevents_launch(self):
        worker.stop_campaign(self.root/"STOP.json","original optical failure",1)
        first=(self.root/"STOP.json").read_bytes()
        worker.stop_campaign(self.root/"STOP.json","later sibling",2)
        self.assertEqual(first,(self.root/"STOP.json").read_bytes())
        with mock.patch.object(worker.subprocess,"Popen",side_effect=AssertionError("must not launch")):
            with mock.patch.dict("os.environ",{"SLURM_ARRAY_TASK_ID":"1"}):
                self.assertEqual(worker.execute(self.bundle,self.manifest_hash),1)

    def test_node_failure_oom_and_timeout_stop_siblings(self):
        worker.verify_sibling_states("7_1|COMPLETED|0:0|\n7_2|RUNNING|0:0|\n")
        for state in ("FAILED","NODE_FAIL","OUT_OF_MEMORY","TIMEOUT","CANCELLED"):
            with self.subTest(state=state),self.assertRaises(ValueError):
                worker.verify_sibling_states(f"7_1|{state}|1:0|\n")

    def test_main_proposal_counts_seeds_and_non_submission(self):
        pairs=[dict(tile_thickness_mm=t,layout=l,conservative_events_per_arm=1000+100*i)
               for i,(t,l) in enumerate(stats.SPEC["pairs"])]
        configs={(c["tile_thickness_mm"],c["layout"],c["gap_mm"]):c for c in self.configs}
        rates={configuration_hash(c):dict(mean=10.,worst=14.) for c in self.configs}
        for key in io.IDENTITIES:self.manifest[key]="a"*64
        result=precision.freeze_main(pairs,configs,self.tasks,[7001,7002],self.manifest,self.root,rates)
        self.assertEqual(result["summary"]["events"],9200)
        self.assertFalse(result["submitted"])
        self.assertFalse((self.root/"formal-plan/array.sbatch").exists())
        main=osc.read(self.root/"formal-plan/tasks.json")
        used={t[k] for t in self.tasks for k in ("seed1","seed2")}|{7001,7002}
        generated=[t[k] for t in main for k in ("seed1","seed2")]
        self.assertEqual(len(generated),len(set(generated)))
        self.assertTrue(used.isdisjoint(generated))

    def test_complete_estimate_freezes_only_a_proposal(self):
        for key in io.IDENTITIES:self.manifest[key]="a"*64
        entries=[dict(task=t,execution={"launcher_elapsed_seconds":200.},
                      numerical={"corrections":0,"boundary_no_rindex":0}) for t in self.tasks]
        def event(entry):
            t=entry["task"]
            x=blocks(t["task_id"],factor=1.1 if t["config"]["gap_mm"]==1. else 1.)[t["block"]]
            return dict(x,task_id=t["task_id"],config=t["config"],fields=FIELDS)
        args=SimpleNamespace(batch_dir=self.root,manifest_sha256=self.manifest_hash,output_dir=self.root/"analysis")
        with mock.patch.object(precision,"accepted_index",return_value=(self.manifest,entries)),\
             mock.patch.object(precision,"verify_scheduler"),mock.patch.object(precision,"event_block",side_effect=event),\
             mock.patch.object(precision,"plots"):
            precision.estimate(args)
        result=osc.read(args.output_dir/"calibration-result.json")
        self.assertTrue(result["passed"])
        self.assertEqual(result["calibration_events"],8000)
        self.assertFalse(result["main_submitted"])
        self.assertFalse(result["calibration_in_main"])
        self.assertEqual(len(osc.read(args.output_dir/"four-comparisons.json")),4)
        self.assertTrue((args.output_dir/"artifact-index.json").exists())

    def test_unfinished_scheduler_status_is_rejected(self):
        entry=dict(execution=dict(scheduler_job="123",array_index=1))
        response=SimpleNamespace(stdout="JobID|State|ExitCode|AllocCPUS|ElapsedRaw|NodeList|\n123_1|RUNNING|0:0|1|1|p0220|\n")
        with mock.patch.object(precision.subprocess,"run",return_value=response),\
             self.assertRaisesRegex(ValueError,"Scheduler completion"):
            precision.verify_scheduler([entry],self.root)


if __name__ == "__main__":unittest.main()
