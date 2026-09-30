"""Offline integrity and recovery checks; never touches production datasets."""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "install_datasets.py"
spec = importlib.util.spec_from_file_location("dataset_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class DatasetInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="g4data-test-")
        self.base = Path(self.temporary.name).resolve()
        self.remote = self.base / "remote"
        self.remote.mkdir()
        self.root = self.base / "installed"
        self.report = self.base / "report.json"
        self.archive = self.remote / "Fixture.1.tar.gz"
        self.payload = b"validated fixture\n"
        with tarfile.open(self.archive, "w:gz") as output:
            member = tarfile.TarInfo("Fixture1/nested/table.txt")
            member.size = len(self.payload)
            member.mode = 0o644
            output.addfile(member, io.BytesIO(self.payload))
        self.entry = {"name": "Fixture", "version": "1", "env": "G4FIXTUREDATA",
                      "directory": "Fixture1", "archive": self.archive.name,
                      "md5": hashlib.md5(self.archive.read_bytes()).hexdigest()}
        self.manifest = self.base / "manifest.json"
        self.write_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self):
        self.manifest.write_text(json.dumps({"schema_version": 1,
            "geant4_version": "11.4.2", "base_url": self.remote.as_uri(),
            "datasets": [self.entry]}), encoding="utf-8")

    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.root),
            "--manifest", str(self.manifest), "--report", str(self.report), *args],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(self.report.read_text()), result

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest())
                for p in self.root.rglob("*") if p.is_file()}

    def test_install_verify_and_reuse_are_content_checked(self):
        report, _ = self.run_cli()
        self.assertEqual(report["datasets"][0]["action"], "installed")
        self.assertEqual(report["environment"]["G4FIXTUREDATA"], str(self.root / "Fixture1"))
        self.assertFalse((self.root / (self.archive.name + ".part")).exists())
        before = self.snapshot()
        self.run_cli("--verify-only")
        self.assertEqual(before, self.snapshot(), "verify-only changed dataset files")
        report, _ = self.run_cli()
        self.assertEqual(report["datasets"][0]["action"], "reused")

    def test_same_size_corruption_is_detected_and_preserved_on_repair(self):
        self.run_cli()
        table = self.root / "Fixture1/nested/table.txt"
        damaged = b"x" * len(self.payload)
        table.write_bytes(damaged)
        report, _ = self.run_cli("--verify-only", expected=1)
        self.assertIn("SHA256 mismatch", report["error"])
        self.assertEqual(table.read_bytes(), damaged)
        report, _ = self.run_cli()
        self.assertEqual(report["datasets"][0]["action"], "repaired")
        self.assertEqual(table.read_bytes(), self.payload)
        saved = list((self.root / ".quarantine").glob("Fixture1.*/nested/table.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), damaged)
        self.run_cli("--verify-only")

    def test_archive_corruption_is_detected_before_directory_reuse(self):
        self.run_cli()
        (self.root / self.archive.name).write_bytes(b"invalid archive")
        report, _ = self.run_cli("--verify-only", expected=1)
        self.assertIn("Archive MD5", report["error"])
        self.run_cli()
        self.assertTrue(list((self.root / ".quarantine").glob(self.archive.name + ".*")))
        self.run_cli("--verify-only")

    def test_interrupted_download_and_unreceipted_directory_recover(self):
        self.root.mkdir()
        raw = self.archive.read_bytes()
        (self.root / (self.archive.name + ".part")).write_bytes(raw[:len(raw) // 2])
        partial_dir = self.root / "Fixture1"
        partial_dir.mkdir()
        (partial_dir / "partial.txt").write_text("unfinished")
        report, _ = self.run_cli()
        self.assertEqual(report["datasets"][0]["action"], "repaired")
        self.assertEqual((self.root / self.archive.name).read_bytes(), raw)
        self.assertTrue(list((self.root / ".quarantine").glob("Fixture1.*/partial.txt")))
        self.run_cli("--verify-only")

    def test_bad_download_never_becomes_an_accepted_archive(self):
        self.entry["md5"] = "0" * 32
        self.write_manifest()
        report, _ = self.run_cli(expected=1)
        self.assertIn("MD5 does not match", report["error"])
        self.assertFalse((self.root / self.archive.name).exists())
        self.assertFalse((self.root / "Fixture1").exists())
        self.assertTrue((self.root / (self.archive.name + ".part")).exists())

    def test_tar_path_escape_is_rejected_before_directory_commit(self):
        with tarfile.open(self.archive, "w:gz") as output:
            member = tarfile.TarInfo("Fixture1/../../escape.txt")
            member.size = 1
            output.addfile(member, io.BytesIO(b"x"))
        self.entry["md5"] = hashlib.md5(self.archive.read_bytes()).hexdigest()
        self.write_manifest()
        report, _ = self.run_cli(expected=1)
        self.assertIn("outside expected dataset", report["error"])
        self.assertFalse((self.root / "Fixture1").exists())
        self.assertFalse((self.base / "escape.txt").exists())
        self.assertFalse(list(self.root.glob(".Fixture1.extract-*")))

    def test_missing_version_receipt_is_not_reported_as_complete(self):
        self.run_cli()
        (self.root / installer.ROOT_RECEIPT).unlink()
        report, _ = self.run_cli("--verify-only", expected=1)
        self.assertIn("Missing version receipt", report["error"])
        report, _ = self.run_cli()
        self.assertEqual(report["datasets"][0]["action"], "reused")
        self.run_cli("--verify-only")

    def test_receipts_remain_valid_after_mount_path_changes(self):
        self.run_cli()
        moved = self.base / "mounted-data"
        self.root.rename(moved)
        self.root = moved
        report, _ = self.run_cli("--verify-only")
        self.assertEqual(report["environment"]["G4FIXTUREDATA"], str(moved / "Fixture1"))

    def test_legacy_absolute_receipt_environment_is_relocatable(self):
        self.run_cli()
        receipt_path = self.root / installer.ROOT_RECEIPT
        receipt = json.loads(receipt_path.read_text())
        receipt.pop("environment_relative")
        receipt_path.write_text(json.dumps(receipt))
        moved = self.base / "mounted-data"
        self.root.rename(moved)
        self.root = moved
        self.run_cli("--verify-only")

    def test_interruption_between_quarantine_and_commit_is_recoverable(self):
        self.run_cli()
        manifest, manifest_hash = installer.load_manifest(self.manifest)
        archive = self.root / self.archive.name
        identity = installer.dataset_identity(manifest, manifest_hash, self.entry,
                                              installer.digest_file(archive))
        original_replace = os.replace
        destination = self.root / "Fixture1"

        def interrupt_commit(source, target):
            if Path(target) == destination:
                raise OSError("simulated interruption immediately before commit")
            return original_replace(source, target)

        with mock.patch.object(installer.os, "replace", side_effect=interrupt_commit):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                installer.extract_and_commit(self.root, archive, self.entry, identity)
        self.assertFalse(destination.exists())
        self.assertTrue(list((self.root / ".quarantine").glob("Fixture1.*")))
        self.run_cli()
        self.run_cli("--verify-only")

    def test_dataset_symlink_is_never_followed(self):
        self.run_cli()
        table = self.root / "Fixture1/nested/table.txt"
        table.unlink()
        external = self.base / "external.txt"
        external.write_bytes(self.payload)
        table.symlink_to(external)
        report, _ = self.run_cli("--verify-only", expected=1)
        self.assertIn("Unexpected dataset file", report["error"])
        self.run_cli()
        self.assertEqual(external.read_bytes(), self.payload)
        self.assertFalse(table.is_symlink())

    def test_lock_prevents_parallel_installation(self):
        self.root.mkdir()
        with (self.root / ".geant4-datasets.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report, _ = self.run_cli(expected=1)
            self.assertIn("Another dataset installation", report["error"])
        self.assertFalse((self.root / self.archive.name).exists())

    def test_verify_missing_root_does_not_create_it(self):
        report, _ = self.run_cli("--verify-only", expected=1)
        self.assertIn("root does not exist", report["error"])
        self.assertFalse(self.root.exists())

    def test_official_manifest_has_the_twelve_required_versions(self):
        manifest, _ = installer.load_manifest(installer.DEFAULT_MANIFEST)
        self.assertEqual(manifest["geant4_version"], "11.4.2")
        versions = {entry["name"]: entry["version"] for entry in manifest["datasets"]}
        self.assertEqual(versions, {"G4NDL": "4.7.1", "G4EMLOW": "8.8",
            "PhotonEvaporation": "6.1.2", "RadioactiveDecay": "6.1.2",
            "G4PARTICLEXS": "4.2", "G4PII": "1.3", "RealSurface": "2.2",
            "G4SAIDDATA": "2.0", "G4ABLA": "3.3", "G4INCL": "1.3",
            "G4ENSDFSTATE": "3.0", "G4CHANNELING": "2.0"})


if __name__ == "__main__":
    unittest.main()
