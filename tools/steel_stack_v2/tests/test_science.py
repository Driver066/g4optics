"""Formal registry preservation, scheduler sequencing, acceptance, and precision."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

SCRIPT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SCRIPT))
import osc
import science
import science_io as io
import science_worker as worker
from model import canonical_json,make_configuration,prepare_tasks,validate_tasks
from precision_io import analysis_identity,save_new
from precision_math import SPEC


def registry():
    tasks=[];used={7001,7002}
    for (t,l),n in io.ALLOCATIONS.items():
        cfg=[make_configuration(l,t,g,"painted-corner-v2",16) for g in (.5,1.)]
        part=prepare_tasks(cfg,events_per_task=100,blocks=n//100,campaign_seed=SPEC["main_seed"],
                           total_event_budget=2*n,stage="science",excluded_seeds=used)
        for task in part:
            task["purpose"]="science";used.update((task["seed1"],task["seed2"]))
        tasks.extend(part)
    return tasks


class MainMathTests(unittest.TestCase):
    def test_ratio_direction_corrected_quantiles_and_precision_pass_or_fail(self):
        ref=dict(statistics=dict(mean=100.),bootstrap_mean=np.linspace(99.,101.,10000))
        good=dict(statistics=dict(mean=104.),bootstrap_mean=np.linspace(103.,105.,10000)[::-1])
        p=science.assess_pair(ref,good,thickness=4,layout="edge-two")
        self.assertAlmostEqual(p["main_relative_change"],.04)
        self.assertEqual(p["corrected9875"]["quantiles"],[.00625,.99375])
        self.assertTrue(p["precision_passed"])
        self.assertNotIn("conservative_events_per_arm",p)
        wide=dict(statistics=dict(mean=100.),bootstrap_mean=np.linspace(20.,180.,10000))
        self.assertFalse(science.assess_pair(ref,wide,thickness=4,layout="edge-two")["precision_passed"])

    def test_invalid_reference_resample_is_not_dropped(self):
        ref=dict(statistics=dict(mean=10.),bootstrap_mean=np.full(10000,10.))
        other=dict(statistics=dict(mean=11.),bootstrap_mean=np.full(10000,11.))
        ref["bootstrap_mean"][13]=0
        p=science.assess_pair(ref,other,thickness=4,layout="back-center")
        self.assertFalse(p["precision_passed"])
        self.assertFalse(p["corrected9875"]["valid"])
        self.assertEqual(p["corrected9875"]["invalid_resamples"],1)
        self.assertIsNone(p["corrected9875"]["low"])


class MainCampaignTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()/"main";self.root.mkdir()
        self.tasks=registry()
        self.proposal=dict(schema_version="steel-gap-main-proposal-v1",state="frozen-proposal-not-submitted",
            submitted=False,executable=True,calibration_included=False,benchmark_included=False,
            main_precision_verified=False,resources=SPEC["resources"],summary=validate_tasks(self.tasks),
            simulation_identity={k:"a"*64 for k in io.IDENTITIES})
        (self.root/"proposal").mkdir();(self.root/"controller").mkdir()
        for name in io.CONTROLLER_FILES:shutil.copyfile(SCRIPT/name,self.root/"controller"/name)
        save_new(self.root/"controller/controller.json",dict(files={n:osc.sha256(self.root/"controller"/n)
            for n in io.CONTROLLER_FILES},analysis_identity=analysis_identity()))
        for path,value in (("tasks.json",self.tasks),("statistics-spec.json",SPEC),
            ("proposal/tasks.json",self.tasks),("proposal/manifest.json",self.proposal),
            ("proposal/calibration-tasks.json",[]),("proposal/excluded-benchmark-seeds.json",[7001,7002])):
            save_new(self.root/path,value)
        control=dict(root=str(self.root/"controller"),sha256=osc.sha256(self.root/"controller/controller.json"),
                     python=sys.executable,stop_file=str(self.root/"STOP.json"))
        bundles=[dict(name=f"array-{i:03d}",path=str(self.root/"bundles"/f"array-{i:03d}"),offset=start,
                       tasks=min(900,len(self.tasks)-start)) for i,start in enumerate(range(0,len(self.tasks),900))]
        self.campaign=dict(schema_version="steel-gap-main-campaign-v1",purpose="science",root=str(self.root),
            execution_authorized=True,resources=SPEC["resources"],array_capacity=900,control=control,
            frozen_inputs={str(p.relative_to(self.root)):osc.sha256(p) for p in
                           [self.root/"tasks.json",self.root/"statistics-spec.json",*sorted((self.root/"proposal").iterdir())]},
            simulation_identity=self.proposal["simulation_identity"],summary=validate_tasks(self.tasks),bundles=bundles)
        save_new(self.root/"campaign.json",self.campaign);self.hash=osc.sha256(self.root/"campaign.json")
        source=self.root.parent/"source";source.mkdir()
        for info in bundles:
            selected=self.tasks[info["offset"]:info["offset"]+info["tasks"]]
            plan=dict(purpose="science",mock=False,tasks=selected,summary=validate_tasks(selected),task_offset=info["offset"],
                optical_numerics=dict(profile="painted-corner-v2",scale=16),local_staging_inputs=dict(source_root=str(source)),
                remote_paths=dict(source_root="/frozen/source",data_root="/frozen/data"),
                science_control={**control,"campaign_root":str(self.root),"campaign_sha256":self.hash},
                **self.proposal["simulation_identity"])
            osc.render(plan,Path(info["path"]),remote_bundle_root=info["path"],remote_output_root=str(self.root/"results"),
                       account="MOCK",time_minutes=60,memory_gib=4,max_parallel=4,node_constraint="40core")
            (Path(info["path"])/"continue.sbatch").write_text("# fixture continuation; never execute\n")
        (self.root/"analysis.sbatch").write_text("# fixture analysis; never execute\n")
        self.store_preparation()
        self.args=SimpleNamespace(batch_dir=self.root,campaign_sha256=self.hash)

    def store_preparation(self):
        files=[p for p in (self.root/"bundles").rglob("*") if p.is_file()]+[self.root/"analysis.sbatch"]
        (self.root/"preparation.json").write_text(json.dumps(dict(campaign_sha256=self.hash,
            artifacts={str(p.relative_to(self.root)):osc.sha256(p) for p in files})))

    def test_full_variable_allocation_and_three_arrays_preserve_frozen_tasks(self):
        c,ts=io.read_campaign(self.root,self.hash)
        self.assertEqual(len(ts),2472);self.assertEqual(sum(t["events"] for t in ts),247200)
        self.assertEqual([b["tasks"] for b in c["bundles"]],[900,900,672])
        self.assertEqual(ts,self.tasks)
        for b in c["bundles"]:
            script=(Path(b["path"])/"array.sbatch").read_text()
            self.assertIn("science_worker.py",script);self.assertIn("--no-requeue",script)
            self.assertIn(f"--array=1-{b['tasks']}%4",script)

    def test_mixed_sample_seed_collision_and_missing_block_are_rejected(self):
        for transform in (lambda ts:ts[:-1],lambda ts:[{**ts[0],"purpose":"calibration"},*ts[1:]],
                          lambda ts:[{**ts[0],"seed1":7001},*ts[1:]]):
            with self.subTest(transform=transform),self.assertRaises(ValueError):
                io.validate_proposal(self.proposal,transform(copy.deepcopy(self.tasks)),[],[7001,7002])

    def test_frozen_controller_and_execution_registry_changes_are_rejected(self):
        (self.root/"controller/science_worker.py").write_text("changed")
        with self.assertRaisesRegex(ValueError,"Controller inventory"):
            io.read_campaign(self.root,self.hash)

    def test_worker_stop_prevents_any_simulation_launch(self):
        save_new(self.root/"STOP.json",dict(error="numerical failure"))
        first=(self.root/"STOP.json").read_bytes()
        bundle=Path(self.campaign["bundles"][0]["path"])
        with mock.patch.object(worker.subprocess,"Popen",side_effect=AssertionError("must not launch")):
            self.assertEqual(worker.execute(bundle,osc.sha256(bundle/"manifest.json")),1)
        self.assertEqual(first,(self.root/"STOP.json").read_bytes())

    def test_submission_is_held_until_receipts_exist_and_waiting_is_idempotent(self):
        calls=[]
        def fake(command,**kwargs):
            calls.append(command)
            if command[0]=="sbatch":
                count=sum(c[0]=="sbatch" for c in calls)
                return SimpleNamespace(returncode=0,stdout=f"{700+count}\n",stderr="")
            if command[:3]==["scontrol","show","job"]:
                return SimpleNamespace(returncode=0,stdout="JobState=PENDING Reason=JobHeldUser",stderr="")
            if command[:2]==["scontrol","release"]:
                self.assertTrue((self.root/"submissions/array-000.json").exists())
                self.assertTrue((self.root/"submissions/continue-array-000.json").exists())
                return SimpleNamespace(returncode=0,stdout="",stderr="")
            if command[0]=="sacct":
                return SimpleNamespace(returncode=0,stdout="JobID|State|ExitCode|AllocCPUS|ElapsedRaw|NodeList|\n701_1|RUNNING|0:0|1|1|fixture|\n",stderr="")
            raise AssertionError(command)
        with mock.patch.object(science.subprocess,"run",side_effect=fake):
            self.assertEqual(science.advance(self.args)["state"],"array-submitted")
            self.assertEqual(science.advance(self.args)["state"],"waiting-for-array")
        self.assertEqual(sum(c[0]=="sbatch" for c in calls),2)
        self.assertIn("--hold",next(c for c in calls if c[0]=="sbatch"))
        self.assertTrue(any("--dependency=afterany:701" in c for c in calls))
        self.assertFalse((self.root/"submissions/array-001.json").exists())

    def test_uncertain_submission_intent_blocks_duplicate_job(self):
        script=Path(self.campaign["bundles"][0]["path"])/"array.sbatch"
        with mock.patch.object(science.subprocess,"run",side_effect=subprocess.TimeoutExpired("sbatch",60)):
            with self.assertRaises(subprocess.TimeoutExpired):science.submit_one(self.root,self.hash,"array-000",script)
        with mock.patch.object(science.subprocess,"run",side_effect=AssertionError("must not submit")):
            with self.assertRaisesRegex(ValueError,"Unresolved earlier submission"):
                science.submit_one(self.root,self.hash,"array-000",script)

    def test_scheduler_complete_without_acceptance_does_not_advance(self):
        directory=self.root/"submissions";directory.mkdir()
        save_new(directory/"array-000.json",dict(job_id="701"))
        save_new(directory/"continue-array-000.json",dict(job_id="702"))
        save_new(directory/"array-000.release.json",dict(job_id="701"))
        rows="JobID|State|ExitCode|AllocCPUS|ElapsedRaw|NodeList|\n"+"\n".join(
            f"701_{i}|COMPLETED|0:0|1|1|fixture|" for i in range(1,901))+"\n"
        with mock.patch.object(science.subprocess,"run",return_value=SimpleNamespace(stdout=rows,stderr="",returncode=0)):
            with self.assertRaisesRegex(ValueError,"Missing/ambiguous formal attempt"):science.advance(self.args)
        self.assertTrue((self.root/"STOP.json").exists())
        self.assertFalse((directory/"array-001.json").exists())

    def test_duplicate_or_failed_scheduler_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError,"Duplicate scheduler"):
            io.scheduler_rows("JobID|State|ExitCode|AllocCPUS|\n7_1|COMPLETED|0:0|1|\n7_1|COMPLETED|0:0|1|\n")
        for state,cpus in (("TIMEOUT","1"),("COMPLETED","2")):
            with self.subTest(state=state),self.assertRaises(ValueError):
                io.require_completed({"7_1":dict(State=state,ExitCode="0:0",AllocCPUS=cpus)},"7",1)


if __name__ == "__main__":unittest.main()
