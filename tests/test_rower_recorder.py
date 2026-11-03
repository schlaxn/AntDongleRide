import csv
import tempfile
import unittest
from pathlib import Path

from rower.recorder import RowRecorder


class RecorderTests(unittest.TestCase):
    def test_speed_is_averaged_per_second_and_strokes_are_timed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = RowRecorder(Path(tmp), 1000.0)
            r.sample({"ts": 1000.1, "metric": "stroke", "value": 1})
            r.sample({"ts": 1000.2, "metric": "code", "value": 9})
            r.sample({"ts": 1000.4, "metric": "speed", "value": 3.6})
            r.sample({"ts": 1000.6, "metric": "code", "value": 8})
            r.sample({"ts": 1000.8, "metric": "speed", "value": 3.4})
            r.sample({"ts": 1001.2, "metric": "stroke_end", "value": 1})
            r.sample({"ts": 1003.5, "metric": "speed", "value": 3.0})   # 1001 and 1002 have no lines
            r.close({})
            speed = list(csv.DictReader(open(r.folder / "speed_1s.csv", encoding="utf-8")))
            strokes = list(csv.DictReader(open(r.folder / "strokes.csv", encoding="utf-8")))
        self.assertEqual([row["t_s"] for row in speed], ["1000", "1001", "1002", "1003"])
        self.assertAlmostEqual(float(speed[0]["speed_ms"]), 3.5, places=3)
        self.assertEqual((speed[0]["ticks"], speed[0]["lines"]), ("17", "2"))
        self.assertEqual((speed[1]["speed_ms"], speed[1]["ticks"], speed[1]["lines"]), ("0.0", "0", "0"))
        self.assertEqual((speed[3]["ticks"], speed[3]["lines"]), ("0", "0"))
        self.assertEqual([(row["event"], float(row["ts"])) for row in strokes],
                         [("SS", 1000.1), ("SE", 1001.2)])
