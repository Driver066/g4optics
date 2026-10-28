"""Offline counterexamples for the scientific pilot's immutable execution ledger."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import pilot
import pilot_model

REPOSITORY = TOOLS.parents[1]
TEMPLATE = (REPOSITORY / pilot_model.TEMPLATE).read_text()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        for relative in pilot.DEPENDENCIES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPOSITORY / relative, target)
        app = self.root / "test/OpNovice2"
        app.mkdir(parents=True)
        (app / "OpNovice2.cc").write_text("// synthetic accepted source\n")
        (self.root / "tools/dimple_mechanism/PILOT.md").write_text("Frozen synthetic scientific protocol\n")
        self.accepted = self.directory / "accepted"
        self.accepted.mkdir()
        (self.accepted / "optical-template.mac").write_text(TEMPLATE)
        (self.accepted / "manifest.json").write_text('{"fixture": true}\n')
        (self.accepted / "binary").write_bytes(b"never execute this offline fixture")
        (self.root / "old.json").write_text('{"cases": [{"seeds": [11, 12]}]}\n')
        self.exclusions = self.root / "exclusions.json"
        self.registry = dict(schema="issue9-pilot-seed-exclusions-v1", seeds=[11,12], sources=[
            dict(manifest="old.json", manifest_sha256=pilot.sha(self.root / "old.json"),
                 seeds=[11,12], verified_geometry_macro_sha256={})])
        self.exclusions.write_text(json.dumps(self.registry))
        self.accepted_manifest = dict(source_sha256={"test/OpNovice2/OpNovice2.cc": pilot.sha(app / "OpNovice2.cc")},
                                     image_id="sha256:" + "a" * 64, dataset_manifest_sha256="b" * 64)
        self.accepted_copies = {"accepted/manifest.json": self.accepted / "manifest.json",
                                "accepted/optical-template.mac": self.accepted / "optical-template.mac",
                                "bin/OpNovice2": self.accepted / "binary"}
        self.batch = self.directory / "pilot-batch"
        context = patch.object(pilot, "ROOT", self.root)
        context.start(); self.addCleanup(context.stop)
        git = patch.object(pilot, "git", return_value=b"offline-fixture\n")
        git.start(); self.addCleanup(git.stop)
        accepted = patch.object(pilot, "accepted_files", return_value=(self.accepted_manifest, self.accepted_copies))
        accepted.start(); self.addCleanup(accepted.stop)

    def prepare(self):
        return pilot.prepare(self.batch, self.accepted, self.exclusions)

    def test_prepare_is_offline_fixed_and_refuses_overwrite(self):
        with patch.object(pilot.subprocess, "run", side_effect=AssertionError("Unexpected process")):
            manifest = self.prepare()
            before = {str(path.relative_to(self.batch)): pilot.sha(path)
                      for path in self.batch.rglob("*") if path.is_file()}
            report = pilot.status(self.batch)
            after = {str(path.relative_to(self.batch)): pilot.sha(path)
                     for path in self.batch.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(manifest["total_tasks"], 350)
        self.assertEqual(manifest["total_events"], 35000)
        self.assertEqual(report["counts"]["unstarted"],350)
        self.assertEqual(report["accepted_events"],0)
        self.assertFalse((self.batch / "preflight.json").exists())
        self.assertFalse(any(self.batch.rglob("simulation.log")))
        with self.assertRaisesRegex(ValueError,"already exists"):
            self.prepare()

    def test_seed_collision_and_false_historical_claim_reject_before_writes(self):
        first = pilot_model.tasks()[0]["seeds"][0]
        (self.root / "old.json").write_text(json.dumps(dict(seeds=[first])))
        self.registry.update(seeds=[first])
        self.registry["sources"][0].update(seeds=[first], manifest_sha256=pilot.sha(self.root / "old.json"))
        self.exclusions.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError,"collide"):
            self.prepare()
        self.assertFalse(self.batch.exists())
        self.registry["seeds"] = [11]
        self.registry["sources"][0]["seeds"] = [11]
        self.exclusions.write_text(json.dumps(self.registry))
        with self.assertRaisesRegex(ValueError,"not supported"):
            self.prepare()
        self.assertFalse(self.batch.exists())

    def test_manifest_macro_source_binary_and_history_tampering(self):
        self.prepare()
        paths = [self.batch / pilot.MANIFEST, self.batch / "bin/OpNovice2",
                 self.batch / "source/test/OpNovice2/OpNovice2.cc",
                 self.batch / "tasks" / pilot_model.tasks()[0]["name"] / "run.mac",
                 self.batch / "seed-history/00-00.json", self.batch / "protocol/PILOT.md"]
        for path in paths:
            with self.subTest(path=str(path.relative_to(self.batch))):
                original = path.read_bytes()
                path.chmod(0o644)
                path.write_bytes(original + b"changed")
                with self.assertRaises((ValueError,json.JSONDecodeError)):
                    pilot.verify(self.batch)
                path.write_bytes(original)
        pilot.verify(self.batch)
        (self.batch / "source/unregistered.py").write_text("# unrecorded runtime dependency")
        with self.assertRaisesRegex(ValueError,"inventory changed"):
            pilot.verify(self.batch)

    def test_running_dependency_change_and_unknown_task_are_rejected(self):
        manifest=self.prepare()
        with self.assertRaisesRegex(ValueError,"Unknown task"):
            pilot.find_task(manifest,"../pilot-b0-A-p00")
        path=self.root / pilot.DEPENDENCIES[1]
        path.write_text(path.read_text()+"\n# changed")
        with self.assertRaisesRegex(ValueError,"Running dependency changed"):
            pilot.verify(self.batch)

    def test_one_atomic_attempt_per_task_and_no_global_worker_limit(self):
        manifest=self.prepare()
        identity=pilot.sha(self.batch/pilot.MANIFEST)
        first, second=manifest["tasks"][:2]
        pilot.claim_task(self.batch,first,identity,{})
        with self.assertRaisesRegex(ValueError,"retry forbidden"):
            pilot.claim_task(self.batch,first,identity,{})
        pilot.claim_task(self.batch,second,identity,{})
        report=pilot.status(self.batch)
        self.assertEqual(report["counts"]["attempted"],2)
        self.assertEqual(report["counts"]["unstarted"],348)
        self.assertFalse((self.batch/".pilot.lock").exists())

    def test_atomic_receipt_publication_refuses_existing_result(self):
        path=self.directory/"receipt.json"
        pilot.save(path,{"identity":"first"})
        with self.assertRaises(FileExistsError):
            pilot.save(path,{"identity":"replacement"})
        self.assertEqual(pilot.read(path),{"identity":"first"})
        self.assertEqual(list(self.directory.glob(".*.tmp")),[])

    def test_failed_process_keeps_attempt_and_refuses_retry(self):
        manifest=self.prepare()
        task=manifest["tasks"][0]
        environment={"fixture":"offline"}
        with patch.object(pilot,"runtime",return_value=environment), \
             patch.object(pilot,"verify_preflight",return_value={}), \
             patch.object(pilot.subprocess,"run",side_effect=subprocess.TimeoutExpired("mock",1)) as process:
            with self.assertRaises(subprocess.TimeoutExpired):
                pilot.run_task(self.batch,task["name"],pilot.sha(self.batch/pilot.MANIFEST),timeout=1)
            with self.assertRaisesRegex(ValueError,"retry forbidden"):
                pilot.run_task(self.batch,task["name"],pilot.sha(self.batch/pilot.MANIFEST),timeout=1)
        self.assertEqual(process.call_count,1)
        directory=self.batch/"tasks"/task["name"]
        self.assertTrue((directory/"attempt.json").is_file())
        self.assertTrue((directory/"failure.json").is_file())
        self.assertFalse((directory/"receipt.json").exists())
        self.assertEqual(pilot.status(self.batch)["counts"]["failed"],1)

    def test_receipt_binds_task_global_inputs_and_output_bytes(self):
        manifest=self.prepare()
        task=manifest["tasks"][0]
        identity=pilot.sha(self.batch/pilot.MANIFEST)
        directory=pilot.claim_task(self.batch,task,identity,{})
        for name in pilot.OUTPUT_NAMES:
            (directory/name).write_text("synthetic file only; no events executed\n")
        receipt=dict(status="passed",task_id=task["name"],input_manifest_sha256=identity,
                     executable_sha256=manifest["executable_sha256"],macro_sha256=task["macro_sha256"],
                     attempt_sha256=pilot.sha(directory/"attempt.json"),process_exit=0,events=100,timeout_seconds=1800,
                     environment={},audit=dict(status="passed",accepted=True,events=100),
                     output_sha256={name:pilot.sha(directory/name) for name in pilot.OUTPUT_NAMES})
        pilot.save(directory/"receipt.json",receipt)
        pilot.verify_receipt(self.batch,manifest,task)
        original=(directory/"receipt.json").read_text()
        bad=copy.deepcopy(receipt);bad["input_manifest_sha256"]="f"*64
        (directory/"receipt.json").write_text(json.dumps(bad))
        with self.assertRaisesRegex(ValueError,"receipt identity"):
            pilot.verify_receipt(self.batch,manifest,task)
        (directory/"receipt.json").write_text(original)
        (directory/"result.root").write_text("mutated")
        with self.assertRaisesRegex(ValueError,"output changed"):
            pilot.verify_receipt(self.batch,manifest,task)

    def test_explicit_manifest_identity_rejects_before_runtime_or_claim(self):
        manifest=self.prepare()
        with patch.object(pilot,"runtime",side_effect=AssertionError("Must reject before runtime")):
            with self.assertRaisesRegex(ValueError,"Explicit input manifest"):
                pilot.run_task(self.batch,manifest["tasks"][0]["name"],"0"*64)
        self.assertFalse(any(self.batch.rglob("attempt.json")))

    def test_failed_preflight_cannot_authorize_execution(self):
        manifest=self.prepare()
        pilot.save(self.batch/"preflight-failure.json",dict(error="interrupted"))
        with self.assertRaisesRegex(ValueError,"failed preflight"):
            pilot.verify_preflight(self.batch,manifest)

    def test_finalize_never_accepts_partial_pilot(self):
        self.prepare()
        with patch.object(pilot,"verify_preflight",return_value={"environment":{}}):
            with self.assertRaises(FileNotFoundError):
                pilot.finalize(self.batch,pilot.sha(self.batch/pilot.MANIFEST))
        self.assertFalse((self.batch/"finalization.json").exists())


if __name__=="__main__": unittest.main()
