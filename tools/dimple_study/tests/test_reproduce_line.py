"""Plan and frozen-input checks; these tests never build or run Geant4."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))
SPEC = importlib.util.spec_from_file_location("reproduce_line", TOOL_DIR / "reproduce_line.py")
reproduction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduction)


def archive_fixture(destination):
    """Copy the distributable inputs only: deliberately no .git or outputs."""
    origin = reproduction.ROOT
    published = json.loads((origin / reproduction.PUBLISHED).read_text())
    files = {origin / relative for relative in published["source_sha256"]}
    files.update((origin / "test/OpNovice2").glob("*.mac"))
    files.update((origin / "test/OpNovice2").glob("*.out"))
    files.update(origin / relative for relative in reproduction.TOOLS)
    files.add(origin / reproduction.PUBLISHED)
    for source in files:
        target = destination / source.relative_to(origin)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


class ReproductionPreparationTests(unittest.TestCase):
    def test_published_plan_and_centre_smoke_seed_reuse(self):
        published = json.loads((reproduction.ROOT / reproduction.PUBLISHED).read_text())
        full = reproduction.selected_tasks(published, "full")
        self.assertEqual(len(full), 52)
        self.assertEqual(sum(case["events"] for case in full), 5200)
        self.assertEqual(len({seed for case in full for seed in case["seeds"]}), 104)
        smoke = reproduction.selected_tasks(published, "smoke")
        self.assertEqual([case["name"] for case in smoke], ["b0-p06-flat", "b0-p06-dimple"])
        self.assertEqual([case["events"] for case in smoke], [5, 5])
        self.assertEqual([case["seeds"] for case in smoke],
                         [[1910081025, 1910081026], [1910081027, 1910081028]])

    def test_prepare_from_archive_then_reject_changed_snapshot_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, output = Path(temporary) / "archive", Path(temporary) / "new-run"
            archive_fixture(archive)
            self.assertFalse((archive / ".git").exists())
            self.assertFalse((archive / "outputs").exists())
            with patch.object(reproduction, "ROOT", archive):
                reproduction.prepare(output, "full")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    reproduction.prepare(output, "full")
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["event_budget"], 5200)
            self.assertTrue((output / "source/test/OpNovice2/OpNovice2.out").is_file())
            self.assertTrue(all(case["macro_sha256"] == case["published_macro_sha256"]
                                for case in manifest["tasks"]))
            with patch.object(reproduction, "__file__", str(output / "runtime" / reproduction.ENTRY)):
                reproduction.verify_snapshot(output)
                extra = output / "source/test/OpNovice2/src/Unexpected.cc"
                extra.write_text("// Must not be compiled into the published model.\n")
                with self.assertRaisesRegex(ValueError, "file set changed"):
                    reproduction.verify_snapshot(output)
                extra.unlink()
                macro = output / "tasks/b0-p00-flat/run.mac"
                macro.write_text(macro.read_text() + "# changed\n")
                with self.assertRaisesRegex(ValueError, "Frozen macro changed"):
                    reproduction.verify_snapshot(output)

    def test_reject_template_drift_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, output = Path(temporary) / "archive", Path(temporary) / "new-run"
            archive_fixture(archive)
            template = archive / reproduction.TEMPLATE
            template.write_text(template.read_text() + "# accidental template change\n")
            with patch.object(reproduction, "ROOT", archive):
                with self.assertRaisesRegex(ValueError, "template differs"):
                    reproduction.prepare(output, "smoke")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
