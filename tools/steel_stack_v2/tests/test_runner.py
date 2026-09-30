import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import uproot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import acceptance
import run as runner


class RunnerTests(unittest.TestCase):
    def test_finite_130_event_registry_and_explicit_replays(self):
        tasks = acceptance.registered_tasks()
        self.assertEqual(len(tasks), 89)
        self.assertEqual(sum(t["events"] for t in tasks), 130)
        self.assertEqual({s: sum(t["events"] for t in tasks if t["stage"] == s)
                          for s in acceptance.STAGES},
                         {"compatibility": 16, "smoke": 48, "noninterference": 64, "repeat": 2})
        by_id = {t["task_id"]: t for t in tasks}
        seen = set()
        for task in tasks:
            pair = task["seed1"], task["seed2"]
            if task["compare_to"]:
                original = by_id[task["compare_to"]]
                self.assertEqual(pair, (original["seed1"], original["seed2"]))
                for key in ("preset", "layout", "tile_thickness_mm", "readout_gap_mm", "events"):
                    self.assertEqual(task[key], original[key])
            else:
                self.assertNotIn(pair, seen)
            seen.add(pair)

    def test_v1_has_no_new_geometry_or_observer_flags(self):
        task = next(t for t in acceptance.registered_tasks() if t["preset"] == "steel-module-stack-v1")
        args = acceptance.scan_args(task)
        self.assertNotIn("--sipm-layout", args)
        self.assertNotIn("--readout-gap-mm", args)
        self.assertNotIn("--stack-photon-accounting", args)

    def test_source_hash_rejects_modified_and_extra_input(self):
        with tempfile.TemporaryDirectory() as directory:
            role = Path(directory)
            (role / "source").mkdir()
            file = role / "source/input.cc"
            file.write_text("original")
            (role / "source-manifest.json").write_text(json.dumps({"source_files": {"input.cc": runner.sha(file)}}))
            runner.verify_source(role)
            (role / "source/extra.cc").write_text("extra")
            with self.assertRaisesRegex(RuntimeError, "source was modified"):
                runner.verify_source(role)
            (role / "source/extra.cc").unlink()
            file.write_text("changed")
            with self.assertRaisesRegex(RuntimeError, "source was modified"):
                runner.verify_source(role)

    def test_task_input_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory)
            tasks = [{"task_id": "one", "events": 2, "config": {"gap": 0.5}}]
            runner.save(batch / "registered-tasks.json", tasks)
            runner.save(batch / "manifest.json", {"registered_tasks_sha256": runner.sha(batch / "registered-tasks.json")})
            runner.save(batch / "tasks.json", [{**tasks[0], "accepted": True}])
            runner.verify_registry(batch)
            runner.save(batch / "tasks.json", [{**tasks[0], "events": 3}])
            with self.assertRaisesRegex(RuntimeError, "scientific identity"):
                runner.verify_registry(batch)

    def test_completed_artifact_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            file = path / "result.root"
            file.write_bytes(b"original")
            receipt = path / "receipt.json"
            audit = path / "audit.json"
            runner.save(audit, {"passed": True})
            runner.save(receipt, {"accepted": True, "task_id": "one", "role": "candidate", "events": 2,
                                   "run_dir": str(path), "audit_sha256": runner.sha(audit),
                                   "artifacts": {"result.root": runner.sha(file)}})
            task = {"task_id": "one", "events": 2, "role": "candidate", "run_dir": str(path),
                    "receipt": str(receipt), "receipt_sha256": runner.sha(receipt), "audit_path": str(audit)}
            runner.verify_completed(task)
            with self.assertRaisesRegex(ValueError, "bound to another task"):
                runner.verify_completed({**task, "task_id": "different-task"})
            file.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "artifact drift"):
                runner.verify_completed(task)

    @staticmethod
    def root(path, value=7, extra=False):
        (path / "outputs").mkdir(parents=True, exist_ok=True)
        with uproot.recreate(path / "outputs/test.root") as root:
            root.mktree("scan", {"event_id": "int32", "generated": "int32", "ce": "float64"})
            root["scan"].extend({"event_id": np.array([0, 1], dtype="i4"),
                                 "generated": np.array([0, value], dtype="i4"),
                                 "ce": np.array([np.nan, .25])})
            root["legacy_hist"] = (np.array([0., 2., 3.]), np.array([0., 1., 2., 3.]))
            if extra:
                root.mktree("stack_event_v2", {"event_id": "int32"})
        (path / "outputs/test.rng-end.txt").write_text("fixed RNG state\n")
        (path / "outputs/test_summary.csv").write_text(f"events,generated\n2,{value}\n")

    def test_exact_comparison_ignores_new_trees_but_rejects_count_or_rng_change(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a", Path(directory) / "b"
            self.root(a)
            self.root(b, extra=True)
            self.assertTrue(acceptance.compare_runs(a, b)["passed"])
            (b / "outputs/test.rng-end.txt").write_text("different RNG state\n")
            with self.assertRaisesRegex(ValueError, "random engine"):
                acceptance.compare_runs(a, b)
            self.root(b, value=8)
            with self.assertRaisesRegex(ValueError, "event values"):
                acceptance.compare_runs(a, b)
            self.root(b)
            (b / "outputs/test_summary.csv").write_text("events,generated\n2,999\n")
            with self.assertRaisesRegex(ValueError, "summary"):
                acceptance.compare_runs(a, b)


if __name__ == "__main__":
    unittest.main()
