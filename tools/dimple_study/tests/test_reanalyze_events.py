"""Public CSV intake tests: preserve identities, integer counts and zero rows."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reanalyze_events", ROOT / "reanalyze_events.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class PublicCSVTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.csv = self.directory / "events.csv"
        tasks, rows = [], []
        for geometry in replay.GEOMETRIES:
            for pi, x in enumerate(replay.POSITIONS):
                for block in (0, 1):
                    i = len(tasks)
                    name = f"{geometry}-p{pi:02d}-b{block}"
                    tasks.append({"name": name, "geometry": geometry, "x_mm": x, "block": block,
                                  "events": 100, "seeds": [10001 + 2*i, 10002 + 2*i]})
                    for event in range(100):
                        # Shared bare event IDs are intentional across all tasks.
                        g = 0 if event == 0 else 10 + event
                        rows.append(dict(task=name, event_id=event, geometry=geometry, x_mm=x,
                            block=block, D=0 if event == 0 else event, G=g,
                            optical_parent_births=0, all_births=g, electron_edep_mev=.2, tile_edep_mev=.21))
        self.manifest = {"tasks": tasks, "event_budget": 5200, "excluded_engineering_seed_values": [7, 8]}
        self.rows = rows

    def write(self, rows):
        with self.csv.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=replay.CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def test_unsorted_rows_keep_all_task_event_ids_and_zeros(self):
        self.write(list(reversed(self.rows)))
        values = replay.read_events(self.csv, self.manifest)
        self.assertEqual(len(values), 52)
        self.assertEqual(sum(len(group) for group in values.values()), 5200)
        for group in values.values():
            self.assertEqual(group.shape, (100, 6))
            np.testing.assert_array_equal(group[:, 0], np.arange(100))
            self.assertEqual(group[0, 1], 0)

    def test_duplicate_identity_is_not_silently_deduplicated(self):
        self.rows[-1] = self.rows[0].copy()
        self.write(self.rows)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            replay.read_events(self.csv, self.manifest)

    def test_fractional_or_float_form_count_is_rejected(self):
        for bad in ("1.5", "1.0", "nan"):
            self.rows[5]["D"] = bad
            self.write(self.rows)
            with self.assertRaisesRegex(ValueError, "integer"):
                replay.read_events(self.csv, self.manifest)

    def test_csv_metadata_cannot_move_event_to_another_cell(self):
        self.rows[4]["x_mm"] = 0
        self.write(self.rows)
        with self.assertRaisesRegex(ValueError, "configuration mismatch"):
            replay.read_events(self.csv, self.manifest)

    def test_missing_event_is_rejected(self):
        self.write(self.rows[:-1])
        with self.assertRaisesRegex(ValueError, "5200"):
            replay.read_events(self.csv, self.manifest)

    def test_existing_output_is_rejected_before_input_loading(self):
        with self.assertRaisesRegex(ValueError, "new output"):
            replay.replay(self.directory / "missing-bundle", self.directory)

    def test_comparison_rejects_changed_interval(self):
        actual = {"configurations": [{"metrics": {"mean_D": {"estimate": 1., "lo95": .8, "hi95": 1.2}}}],
                  "contrasts": [], "symmetry": []}
        published = {"configurations": [{"metrics": {"mean_D": {"estimate": 1., "lo95": .8, "hi95": 1.3}}}],
                     "contrasts": [], "symmetry": []}
        with self.assertRaisesRegex(ValueError, "numeric mismatch"):
            replay.compare_results(actual, published)


if __name__ == "__main__":
    unittest.main()
