"""Reject stale or wrong-readout qualification inputs without running Geant4."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qualify import check_report_binding, safe_child


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class QualificationBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.repo = self.base / 'repo'; self.repo.mkdir()
        (self.repo / 'source.cc').write_text('frozen source\n')
        self.exe = self.base / 'binary'; self.exe.write_bytes(b'frozen binary')
        self.campaign = self.base / 'smoke'; self.campaign.mkdir()
        self.manifest = {'phase': 'smoke', 'configuration': {'readout_mode': 'boundary-entry'},
                         'source_files_sha256': {'source.cc': sha(self.repo / 'source.cc')}}
        self.manifest_path = self.campaign / 'manifest.json'
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.report = self.base / 'smoke-audit.json'
        self.saved = {'status': 'passed', 'phase': 'smoke', 'readout_mode': 'boundary-entry',
                      'manifest_sha256': sha(self.manifest_path), 'executable_sha256': sha(self.exe),
                      'source_files_sha256': self.manifest['source_files_sha256']}
        self.save_report()

    def tearDown(self):
        self.temporary.cleanup()

    def save_report(self):
        self.report.write_text(json.dumps(self.saved))

    def check(self):
        return check_report_binding(self.report, self.campaign, self.repo, self.exe, 'smoke', 'boundary-entry')

    def test_valid_binding_reads_actual_files(self):
        saved, manifest = self.check()
        self.assertEqual(saved['status'], 'passed')
        self.assertEqual(manifest, self.manifest)

    def test_bad_manifest_hash_is_rejected(self):
        self.saved['manifest_sha256'] = '0' * 64; self.save_report()
        with self.assertRaisesRegex(ValueError, 'manifest hash mismatch'):
            self.check()

    def test_legacy_mode_cannot_qualify_corrected_smoke(self):
        self.saved['readout_mode'] = 'legacy'; self.save_report()
        with self.assertRaisesRegex(ValueError, 'readout mode mismatch'):
            self.check()

    def test_stale_source_or_binary_rejected(self):
        (self.repo / 'source.cc').write_text('changed source\n')
        with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
            self.check()
        (self.repo / 'source.cc').write_text('frozen source\n')
        self.exe.write_bytes(b'changed binary')
        with self.assertRaisesRegex(ValueError, 'executable hash mismatch'):
            self.check()

    def test_artifact_path_cannot_escape_campaign(self):
        with self.assertRaisesRegex(ValueError, 'escapes'):
            safe_child(self.campaign, '../unrelated-file')


if __name__ == '__main__':
    unittest.main()
