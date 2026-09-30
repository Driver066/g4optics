"""Manager safety regressions using temporary receipts and mocked subprocesses."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "g4env.py"
spec = importlib.util.spec_from_file_location("local_g4env", SCRIPT)
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ManagerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="g4env-mock-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.app = self.root / "test/OpNovice2"
        self.here = self.root / "tools/local"
        self.envroot = self.root / "outputs/environment"
        self.batch = self.envroot / "acceptance"
        self.data = self.root / "data/11.4.2"
        for directory in (self.app / "src", self.app / "include", self.here, self.batch, self.data):
            directory.mkdir(parents=True, exist_ok=True)
        self.config = dict(manager.CONFIG, data_root=str(self.data))
        self.binary = self.app / self.config["build_dir"] / "OpNovice2"
        self.binary.parent.mkdir()
        self.binary.write_bytes(b"accepted executable")
        self.binary.with_name("CMakeCache.txt").write_text("accepted cache\n")
        (self.app / "CMakeLists.txt").write_text("project(Fixture)\n")
        (self.app / "OpNovice2.cc").write_text("int main() {}\n")
        self.source = self.app / "src/DetectorConstruction.cc"
        self.source.write_text("// accepted source\n")
        (self.root / "Dockerfile").write_text("FROM pinned-test-image\n")
        (self.here / "geant4-11.4.2-datasets.json").write_text(json.dumps({
            "datasets": [{"env": "G4LEDATA", "directory": "G4EMLOW8.8"}]}))
        (self.data / "geant4-datasets-receipt.json").write_text("{}\n")
        self.active = self.envroot / "active.json"
        self.image_lock = self.envroot / "image-lock.json"
        self.image_id = "sha256:accepted-image"
        self.existing = {"Image": self.image_id, "State": {"Running": True},
                         "Mounts": [{"Destination": "/g4data", "Source": str(self.data), "RW": False},
                                    {"Destination": "/work", "Source": str(self.root.parent), "RW": True}]}
        for name, value in {"REPO": self.root, "APP": self.app, "HERE": self.here,
                            "ENVROOT": self.envroot, "ACTIVE": self.active,
                            "IMAGE_LOCK": self.image_lock, "CONFIG": self.config}.items():
            patch = mock.patch.object(manager, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        # Any accidental escape from the manager mocks must fail before a real
        # Docker command, installation, or Geant4 process can execute.
        subprocess_patch = mock.patch.object(manager.subprocess, "run",
            side_effect=AssertionError("Unexpected real subprocess in manager test"))
        subprocess_patch.start()
        self.addCleanup(subprocess_patch.stop)
        for name, value in (("run", ""), ("dexec", "11.4.2"), ("inspect", self.existing)):
            patch = mock.patch.object(manager, name, return_value=value)
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)
        identity_patch = mock.patch.object(manager, "identity")
        self.identity = identity_patch.start()
        self.addCleanup(identity_patch.stop)
        launchers_patch = mock.patch.object(manager, "install_launchers")
        self.install_launchers = launchers_patch.start()
        self.addCleanup(launchers_patch.stop)
        self.initial_lock = {"base_image": manager.IMAGE, "image_id": self.image_id,
                             "dockerfile_sha256": manager.sha(self.root / "Dockerfile")}
        manager.save(self.image_lock, self.initial_lock)

    def seal_acceptance(self):
        seal = manager.acceptance_identity()
        manager.save(self.batch / "acceptance-identity.json", seal)
        manager.save(self.batch / "build-receipt.json", {
            "inputs": seal["build_inputs"], "executable_sha256": seal["executable_sha256"]})
        evidence = {"passed": True, "executable_sha256": seal["executable_sha256"],
                    "image_id": seal["image_id"], "evidence": ["mock receipt"]}
        manager.save(self.batch / "external_gui.json", evidence)
        manager.save(self.batch / "restart-check.json", evidence)
        return seal

    def invoke(self, *args):
        with mock.patch.object(sys, "argv", ["g4env", *args]), contextlib.redirect_stdout(io.StringIO()):
            manager.main()

    def test_matched_acceptance_can_activate_and_keep_previous_default(self):
        self.seal_acceptance()
        old = {"version": "11.3.2", "container": self.config["legacy_container"]}
        manager.save(self.active, old)
        manager.check(self.batch, activate=True)
        self.assertEqual(json.loads(self.active.read_text())["version"], "11.4.2")
        self.assertEqual(json.loads((self.batch / "previous-active.json").read_text()), old)

    def test_skipped_gui_cannot_activate(self):
        self.seal_acceptance()
        with self.assertRaisesRegex(RuntimeError, "GUI"):
            manager.check(self.batch, activate=True, skip_gui=True)
        self.assertFalse(self.active.exists())

    def test_unconfirmed_gui_cannot_activate(self):
        self.seal_acceptance()
        evidence = json.loads((self.batch / "external_gui.json").read_text())
        evidence["passed"] = False
        manager.save(self.batch / "external_gui.json", evidence)
        with self.assertRaisesRegex(RuntimeError, "GUI"):
            manager.check(self.batch, activate=True)
        self.assertFalse(self.active.exists())

    def test_mismatched_gui_image_cannot_activate(self):
        self.seal_acceptance()
        evidence = json.loads((self.batch / "external_gui.json").read_text())
        evidence["image_id"] = "sha256:other-image"
        manager.save(self.batch / "external_gui.json", evidence)
        with self.assertRaisesRegex(RuntimeError, "another executable/image"):
            manager.check(self.batch, activate=True)
        self.assertFalse(self.active.exists())

    def test_source_changed_since_smoke_cannot_activate(self):
        self.seal_acceptance()
        self.source.write_text("// changed source\n")
        with self.assertRaisesRegex(RuntimeError, "differs|changed"):
            manager.check(self.batch, activate=True)
        self.assertFalse(self.active.exists())

    def test_source_changed_during_verification_cannot_activate(self):
        self.seal_acceptance()

        def mutate_during_verification(args, **kwargs):
            if any(Path(str(arg)).name == "verify_environment.py" for arg in args):
                self.source.write_text("// source edited while verification was running\n")
            return ""

        self.run.side_effect = mutate_during_verification
        with self.assertRaisesRegex(RuntimeError, "differs|changed"):
            manager.check(self.batch, activate=True)
        self.assertFalse(self.active.exists())

    def test_smoke_refuses_changed_source_before_running_any_task(self):
        self.seal_acceptance()
        self.source.write_text("// changed before smoke\n")
        with self.assertRaisesRegex(RuntimeError, "Build receipt"):
            manager.smoke(self.batch)
        self.dexec.assert_not_called()

    def test_smoke_preserves_an_interrupted_attempt(self):
        self.seal_acceptance()
        manager.save(self.batch / "tasks.partial.json", [{"task_id": "partial"}])
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            manager.smoke(self.batch)
        self.dexec.assert_not_called()

    def test_new_scan_overrides_stale_dataset_paths(self):
        with mock.patch.object(manager, "start_new"), mock.patch.dict(os.environ, {
                "G4LEDATA": "/old/G4EMLOW8.6.1", "Geant4_DIR": "/old/cmake"}, clear=True):
            self.invoke("scan", "--version", "11.4.2", "--", "full", "custom", "--events", "1")
        env = self.run.call_args.kwargs["env"]
        self.assertEqual(env["G4LEDATA"], "/g4data/G4EMLOW8.8")
        self.assertEqual(env["Geant4_DIR"], "/opt/geant4/lib/cmake/Geant4")
        self.assertEqual(env["G4_DOCKER_BUILD_DIR"], self.config["build_dir"])

    def test_new_scan_rejects_old_build_directory_override(self):
        with mock.patch.object(manager, "start_new"), mock.patch.dict(os.environ, {
                "G4_DOCKER_BUILD_DIR": "build"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Conflicting"):
                self.invoke("scan", "--version", "11.4.2", "--", "full", "custom")
        self.run.assert_not_called()

    def test_legacy_scan_uses_separate_build_and_removes_new_data_env(self):
        with mock.patch.dict(os.environ, {"G4LEDATA": "/g4data/G4EMLOW8.8",
                "G4DATA": "/g4data", "Geant4_DIR": "/new/cmake"}, clear=True):
            self.invoke("scan", "--version", "11.3.2", "--", "full", "custom")
        env = self.run.call_args.kwargs["env"]
        self.assertEqual(env["G4_DOCKER_CONTAINER"], self.config["legacy_container"])
        self.assertEqual(env["G4_DOCKER_BUILD_DIR"], self.config["legacy_build_dir"])
        self.assertNotEqual(env["G4_DOCKER_BUILD_DIR"], "build")
        self.assertNotIn("G4LEDATA", env)
        self.assertNotIn("G4DATA", env)

    def test_shell_separator_preserves_target_version_option(self):
        with mock.patch.object(manager, "start_new"):
            self.invoke("shell", "--version", "11.4.2", "--", "python3", "--version")
        self.assertEqual(self.dexec.call_args.args[0], ["python3", "--version"])

    def test_legacy_version_is_rejected_for_installation_commands(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                self.invoke("setup", "--version", "11.3.2")
        self.assertEqual(error.exception.code, 2)
        self.run.assert_not_called()

    def test_missing_legacy_container_leaves_active_default_untouched(self):
        manager.save(self.active, {"version": "11.4.2"})
        before = self.active.read_bytes()
        self.inspect.return_value = None
        with self.assertRaisesRegex(RuntimeError, "Legacy container"):
            self.invoke("rollback")
        self.assertEqual(self.active.read_bytes(), before)

    def test_wrong_legacy_image_leaves_active_default_untouched(self):
        manager.save(self.active, {"version": "11.4.2"})
        before = self.active.read_bytes()
        self.inspect.return_value = {"Image": "sha256:not-the-retained-legacy-image"}
        with self.assertRaisesRegex(RuntimeError, "Legacy|legacy|image"):
            self.invoke("rollback")
        self.assertEqual(self.active.read_bytes(), before)

    def test_rollback_retains_new_environment_and_saves_active_pointer(self):
        active = {"version": "11.4.2", "container": manager.CONTAINER}
        manager.save(self.active, active)
        self.inspect.return_value = {"Image": self.config["legacy_image_id"]}
        self.invoke("rollback")
        self.assertFalse(self.active.exists())
        copies = list(self.envroot.glob("active-before-rollback-*.json"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(json.loads(copies[0].read_text()), active)
        self.run.assert_not_called()

    def test_failed_reinstallation_does_not_replace_working_image_lock(self):
        before = self.image_lock.read_bytes()
        self.run.side_effect = lambda args, **kwargs: (
            "sha256:new-candidate-image" if "inspect" in args else "")
        with mock.patch.object(manager, "backup"), mock.patch.object(manager, "freeze"), \
                mock.patch.object(manager, "build", side_effect=RuntimeError("candidate build failed")):
            with self.assertRaisesRegex(RuntimeError, "failed|image|existing|Existing"):
                manager.setup(self.batch)
        self.assertEqual(self.image_lock.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
