"""Offline OSC rendering contracts; every external process is forbidden."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT))
import osc


class OfflineOSCTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="osc-static-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "candidate/source"
        self.source.mkdir(parents=True)
        for relative, original in (
                (osc.SOURCE_MODEL, SCRIPT / "model.py"),
                (osc.SOURCE_DATASETS, SCRIPT.parent / "local/geant4-11.4.2-datasets.json")):
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        runner = self.source / osc.SOURCE_RUNNER
        runner.parent.mkdir(parents=True)
        runner.write_text("#!/bin/bash\n# Fake frozen runner, never executed.\n")
        self.source_manifest = self.source.parent / "source-manifest.json"
        osc.write(self.source_manifest, {"schema_version": "steel-stack-v2-frozen-source-v1",
            "git_commit": "1" * 40, "git_status": " M test/OpNovice2/src/Example.cc",
            "source_files": {p.relative_to(self.source).as_posix(): osc.sha256(p)
                             for p in self.source.rglob("*") if p.is_file()}})
        self.binary = self.root / "OpNovice2"
        self.write_elf(62)
        self.sif = self.root / "geant4.sif"
        self.sif.write_bytes(b"mock SIF identity, not executable")
        self.dataset_receipt = self.root / "geant4-datasets-receipt.json"
        catalog_path = self.source / osc.SOURCE_DATASETS
        catalog = osc.read(catalog_path)
        entries = [{**entry, "file_count": 1,
                    "receipt_sha256": hashlib.sha256((entry["name"]+"receipt").encode()).hexdigest(),
                    "archive_sha256": hashlib.sha256((entry["name"]+"archive").encode()).hexdigest()}
                   for entry in catalog["datasets"]]
        osc.write(self.dataset_receipt, {"schema_version": 1, "complete": True,
            "geant4_version": "11.4.2", "manifest_sha256": osc.sha256(catalog_path), "datasets": entries})
        self.receipt = {"schema_version": osc.BUILD_SCHEMA, "mock": True,
            "os": "linux", "architecture": "x86_64", "geant4_version": "11.4.2", "run_manager": "Serial",
            "source_manifest_sha256": osc.sha256(self.source_manifest),
            "source_root_remote": "/fs/scratch/fixture/source", "data_root_remote": "/fs/project/fixture/data-11.4.2",
            "executable": {"path": str(self.binary), "sha256": osc.sha256(self.binary),
                           "remote_path": "/fs/scratch/fixture/bin/OpNovice2"},
            "sif": {"path": str(self.sif), "sha256": osc.sha256(self.sif), "architecture": "x86_64",
                    "geant4_version": "11.4.2", "remote_path": "/fs/project/fixture/geant4.sif"},
            "dataset_receipt": {"path": str(self.dataset_receipt), "sha256": osc.sha256(self.dataset_receipt)}}
        self.build_receipt = self.root / "remote-build.json"
        self.store_receipt()
        self.scheduler_calls = []

        def no_process(*args, **kwargs):
            self.scheduler_calls.append(args)
            raise AssertionError("Real scheduler, network, or process execution is forbidden in these tests")

        for name in ("run", "Popen", "check_call", "check_output", "call"):
            patch = mock.patch.object(subprocess, name, side_effect=no_process)
            patch.start()
            self.addCleanup(patch.stop)
        self.parameters = dict(matrix="sensitivity", gaps=[.5, 1.], events_per_task=3,
                               blocks=2, campaign_seed=12345, total_event_budget=48, allow_mock=True)
        self.output = self.root / "rendered"

    def write_elf(self, machine):
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", header, 16, 3, machine)
        self.binary.write_bytes(header + b"mock ELF, never executed")
        self.binary.chmod(0o755)

    def store_receipt(self):
        osc.write(self.build_receipt, self.receipt)

    def validate(self, **overrides):
        self.store_receipt()
        return osc.validate_inputs(self.source_manifest, self.build_receipt,
                                   **{**self.parameters, **overrides})

    def render(self, plan=None, **overrides):
        return osc.render(plan or self.validate(), self.output,
            **{**dict(remote_bundle_root="/fs/scratch/fixture/rendered",
                      remote_output_root="/fs/scratch/fixture/results", account="MOCK0000",
                      time_minutes=60, memory_gib=4, max_parallel=4), **overrides})

    def test_render_is_offline_and_has_one_serial_cpu_per_task(self):
        result = self.render()
        self.assertFalse(result["submitted"])
        self.assertFalse(result["remote_runtime_verified"])
        self.assertTrue(result["mock"])
        self.assertEqual(result["summary"]["events"], 48)
        script = (self.output / "array.sbatch").read_text()
        for item in ("#SBATCH --cpus-per-task=1", "#SBATCH --ntasks=1", "#SBATCH --array=1-16%4",
                     "G4RUN_MANAGER_TYPE=Serial", "PYTHONDONTWRITEBYTECODE=1"):
            self.assertIn(item, script)
        self.assertEqual(self.scheduler_calls, [])
        self.assertNotIn("ssh ", script)
        self.assertNotIn("sbatch ", script)
        self.assertNotIn("RN_ATTEMPT", script)
        ast.parse((self.output / "array_task.py").read_text())

    def test_mock_worker_refuses_execution_before_any_scheduler_call(self):
        self.render()
        manifest = self.output / "manifest.json"
        worker = self.output / "array_task.py"
        with mock.patch.object(sys, "argv", [str(worker), str(manifest), osc.sha256(manifest)]):
            with self.assertRaisesRegex(RuntimeError, "Mock render cannot execute"):
                exec(compile(worker.read_text(), str(worker), "exec"), {"__file__": str(worker), "__name__": "__main__"})
        self.assertEqual(self.scheduler_calls, [])

    def test_explicit_node_constraint_is_recorded_without_changing_single_cpu(self):
        result = self.render(node_constraint="40core")
        self.assertEqual(result["scheduler"]["node_constraint"], "40core")
        script = (self.output/"array.sbatch").read_text()
        self.assertIn("#SBATCH --constraint=40core\n", script)
        self.assertIn("#SBATCH --cpus-per-task=1\n", script)
        self.assertIn("#SBATCH --mem=4G\n", script)

    def test_unknown_or_injected_node_constraints_are_rejected(self):
        for value in ("", "unknown", "40core\n#SBATCH --exclusive"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "node constraint"):
                self.render(node_constraint=value)
        self.assertFalse(self.output.exists())

    def test_mock_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "allow-mock"):
            self.validate(allow_mock=False)

    def test_aarch64_elf_is_rejected_even_with_claimed_x86_receipt(self):
        self.write_elf(183)
        self.receipt["executable"]["sha256"] = osc.sha256(self.binary)
        with self.assertRaisesRegex(ValueError, "EM_X86_64"):
            self.validate()

    def test_macos_binary_is_rejected(self):
        self.binary.write_bytes(b"\xcf\xfa\xed\xfe" + bytes(96))
        self.receipt["executable"]["sha256"] = osc.sha256(self.binary)
        with self.assertRaisesRegex(ValueError, "ELF64"):
            self.validate()

    def test_architecture_and_version_receipts_are_mandatory(self):
        for key, wrong in (("architecture", "aarch64"), ("geant4_version", "11.3.2"), ("run_manager", "MT")):
            original = self.receipt[key]
            self.receipt[key] = wrong
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()
            self.receipt[key] = original

    def test_remote_binary_must_bind_the_same_source_manifest(self):
        self.receipt["source_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "another frozen source"):
            self.validate()

    def test_changed_source_or_added_file_is_rejected(self):
        runner = self.source / osc.SOURCE_RUNNER
        original = runner.read_bytes()
        runner.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source file inventory/hash"):
            self.validate()
        runner.write_bytes(original)
        (self.source / "unregistered.cc").write_text("extra")
        with self.assertRaisesRegex(ValueError, "source file inventory/hash"):
            self.validate()

    def test_sif_hash_and_dataset_version_are_checked(self):
        self.sif.write_bytes(b"changed SIF")
        with self.assertRaisesRegex(ValueError, "SIF SHA256"):
            self.validate()
        self.receipt["sif"]["sha256"] = osc.sha256(self.sif)
        data = osc.read(self.dataset_receipt)
        data["geant4_version"] = "11.3.2"
        osc.write(self.dataset_receipt, data)
        self.receipt["dataset_receipt"]["sha256"] = osc.sha256(self.dataset_receipt)
        with self.assertRaisesRegex(ValueError, "Dataset receipt"):
            self.validate()

    def test_old_emlow_cannot_hide_in_a_new_version_directory(self):
        data = osc.read(self.dataset_receipt)
        next(e for e in data["datasets"] if e["name"] == "G4EMLOW")["version"] = "8.6.1"
        osc.write(self.dataset_receipt, data)
        self.receipt["dataset_receipt"]["sha256"] = osc.sha256(self.dataset_receipt)
        with self.assertRaisesRegex(ValueError, "package/version"):
            self.validate()

    def test_scientific_budget_is_explicit_and_enforced(self):
        with self.assertRaisesRegex(ValueError, "exceed"):
            self.validate(total_event_budget=47)
        with self.assertRaisesRegex(ValueError, "events_per_task"):
            self.validate(events_per_task=None)
        with self.assertRaisesRegex(ValueError, "blocks"):
            self.validate(blocks=None)
        with self.assertRaisesRegex(ValueError, "total_event_budget"):
            self.validate(total_event_budget=None)

    def test_independent_tasks_have_unique_seeds_and_explicit_gaps(self):
        plan = self.validate()
        tasks = plan["tasks"]
        seeds = [t[k] for t in tasks for k in ("seed1", "seed2")]
        self.assertEqual(len(set(seeds)), 2 * len(tasks))
        self.assertEqual({t["config"]["gap_mm"] for t in tasks}, {.5, 1.})
        self.assertTrue(all(t["independent_sample"] and not t["reproducibility_check"] for t in tasks))
        for task in tasks:
            args = osc.scan_args(task)
            self.assertEqual(args[args.index("--stack-photon-accounting") + 1], "on")
            self.assertNotIn("--source-mode", args)

    def test_promoted_numerics_survive_validation_and_rendering(self):
        plan = self.validate(optical_numerics="painted-corner-v2", corner_scale=16)
        self.assertEqual(plan["optical_numerics"], {"profile":"painted-corner-v2","scale":16})
        self.render(plan)
        tasks = osc.read(self.output/"tasks.json")
        for task in tasks:
            self.assertEqual(task["config"]["optical_numerics"], plan["optical_numerics"])
            args = task["scan_args"]
            self.assertEqual(args[args.index("--optical-numerics")+1], "painted-corner-v2")
            self.assertEqual(args[args.index("--optical-corner-scale")+1], "16")

    def test_benchmark_is_distinct_from_scientific_samples(self):
        science = self.validate()
        bench = self.validate(purpose="benchmark", optical_numerics="painted-corner-v2", corner_scale=16)
        self.assertTrue(all(t["stage"]=="benchmark" and t["purpose"]=="engineering-benchmark-not-scientific-evidence"
                            for t in bench["tasks"]))
        self.assertNotEqual(science["tasks"][0]["seed1"], bench["tasks"][0]["seed1"])
        self.render(bench)
        worker = (self.output/"array_task.py").read_text()
        for field in ("launcher_elapsed_seconds", "child_max_rss_kib", "child_user_seconds"):
            self.assertIn(field, worker)

    def test_calibration_requires_audited_controller_and_keeps_distinct_seeds(self):
        plan = self.validate(purpose="calibration", optical_numerics="painted-corner-v2", corner_scale=16)
        self.assertTrue(all(t["stage"] == "calibration" and t["purpose"] == "sample-size-calibration-only"
                            for t in plan["tasks"]))
        with self.assertRaisesRegex(ValueError,"precision.py prepare"):
            self.render(plan)
        self.assertFalse(self.output.exists())
        plan["calibration_control"] = {"python":"/frozen/venv/bin/python", "root":"/frozen/controller"}
        result = self.render(plan, node_constraint="40core")
        script = (self.output/"array.sbatch").read_text()
        self.assertIn("precision_worker.py",script)
        self.assertIn("--signal=B:TERM@60",script)
        self.assertIn("--no-requeue",script)
        self.assertIn("check_stop()",(self.output/"array_task.py").read_text())

    def test_invalid_numerical_scale_is_rejected_before_render(self):
        for profile, scale in (("legacy",16),("painted-corner-v2",0),("painted-corner-v2",128)):
            with self.subTest(profile=profile, scale=scale), self.assertRaisesRegex(ValueError, "numerical profile"):
                self.validate(optical_numerics=profile, corner_scale=scale)

    def test_render_never_overwrites_an_existing_plan(self):
        self.render()
        before = (self.output / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.render()
        self.assertEqual((self.output / "manifest.json").read_bytes(), before)

    def test_render_cannot_write_inside_local_frozen_source(self):
        self.output = self.source / "new-render"
        with self.assertRaisesRegex(ValueError, "local frozen source"):
            self.render()
        self.assertFalse(self.output.exists())

    def test_render_rejects_shell_injection_and_writes_into_source(self):
        for fields in ({"account": "name\n#SBATCH --cpus-per-task=9"},
                       {"remote_bundle_root": "/tmp/$(bad)"},
                       {"remote_output_root": "/fs/scratch/fixture/source/results"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.render(**fields)
        self.assertFalse(self.output.exists())

    def test_cli_has_no_execution_or_submission_subcommand(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                osc.main(["submit"])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(self.scheduler_calls, [])


if __name__ == "__main__":
    unittest.main()
