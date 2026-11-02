"""Writes one rowing session to recordings/rows/<timestamp>/:

  row.csv       one row per whole second of running time
  samples.csv   every accepted sample (raw, as decoded)
  session.json  workout, events and summary (written on close)
"""
import csv
import json
import time
from pathlib import Path

ROW_COLUMNS = [
    "elapsed_s", "iso_time", "phase", "block", "rep", "hr_bpm", "stroke_rate_spm",
    "speed_ms", "split_s", "power_w", "avg_power_w", "strokes", "distance_m",
    "virtual_speed_ms", "virtual_distance_m", "target_w",
]
SAMPLE_COLUMNS = ["ts", "devtype", "devnum", "metric", "value", "unit"]


class RowRecorder:
    def __init__(self, root: Path, started_at: float):
        stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(started_at))
        self.folder = Path(root) / stamp
        self.folder.mkdir(parents=True, exist_ok=True)
        self._row_f = open(self.folder / "row.csv", "w", newline="", encoding="utf-8")
        self._row_w = csv.DictWriter(self._row_f, fieldnames=ROW_COLUMNS, restval="")
        self._row_w.writeheader()
        self._sample_f = open(self.folder / "samples.csv", "w", newline="", encoding="utf-8")
        self._sample_w = csv.DictWriter(self._sample_f, fieldnames=SAMPLE_COLUMNS, restval="")
        self._sample_w.writeheader()
        self.closed = False

    def row(self, r: dict) -> None:
        if self.closed:
            return
        self._row_w.writerow({c: r.get(c) for c in ROW_COLUMNS})
        self._row_f.flush()

    def sample(self, s: dict) -> None:
        if self.closed:
            return
        self._sample_w.writerow({c: s.get(c) for c in SAMPLE_COLUMNS})
        self._sample_f.flush()

    def close(self, document: dict) -> None:
        if self.closed:
            return
        self.closed = True
        self._row_f.close()
        self._sample_f.close()
        (self.folder / "session.json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
