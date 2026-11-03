"""Writes one rowing session to recordings/rows/<timestamp>/:

  row.csv       one row per whole second of running time (the display values)
  samples.csv   every sample as decoded (speed per line, codes, strokes, power, distance)
  speed_1s.csv  raw speed and ticks averaged over each whole second, for post-processing
                (a second with no line has ticks 0 and speed empty; gaps are filled with zero rows)
  strokes.csv   the sensor's stroke markers: SS (drive starts) and SE (drive ends), with time
  session.json  workout, events and summary (written on close)
"""
import csv
import json
import time
from pathlib import Path

ROW_COLUMNS = [
    "elapsed_s", "iso_time", "phase", "block", "rep", "hr_bpm", "stroke_rate_spm",
    "speed_ms", "split_s", "power_w", "avg_power_w", "strokes", "distance_m",
    "target_w",
]
SAMPLE_COLUMNS = ["ts", "devtype", "devnum", "metric", "value", "unit"]
SPEED_COLUMNS = ["t_s", "speed_ms", "ticks", "lines"]
STROKE_COLUMNS = ["ts", "event"]
STROKE_EVENTS = {"stroke": "SS", "stroke_end": "SE"}


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
        self._speed_f = open(self.folder / "speed_1s.csv", "w", newline="", encoding="utf-8")
        self._speed_w = csv.DictWriter(self._speed_f, fieldnames=SPEED_COLUMNS, restval="")
        self._speed_w.writeheader()
        self._stroke_f = open(self.folder / "strokes.csv", "w", newline="", encoding="utf-8")
        self._stroke_w = csv.DictWriter(self._stroke_f, fieldnames=STROKE_COLUMNS, restval="")
        self._stroke_w.writeheader()
        self._bin_t: int | None = None      # the whole second being averaged
        self._bin_speeds: list[float] = []
        self._bin_ticks = 0
        self._bin_lines = 0
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
        metric = s.get("metric")
        if metric in STROKE_EVENTS:
            self._stroke_w.writerow({"ts": s.get("ts"), "event": STROKE_EVENTS[metric]})
            self._stroke_f.flush()
        elif metric in ("speed", "code"):
            self._add_to_second(s)

    def _add_to_second(self, s: dict) -> None:
        second = int(s["ts"])
        if self._bin_t is not None and second != self._bin_t:
            self._flush_second()
            for missing in range(self._bin_t + 1, second):    # no lines in between: zero ticks
                self._speed_w.writerow({"t_s": missing, "speed_ms": 0.0, "ticks": 0, "lines": 0})
            self._speed_f.flush()
        self._bin_t = second
        if s["metric"] == "speed":
            self._bin_speeds.append(float(s["value"]))
        else:
            self._bin_ticks += int(s["value"])
            self._bin_lines += 1

    def _flush_second(self) -> None:
        if self._bin_t is None:
            return
        speed = round(sum(self._bin_speeds) / len(self._bin_speeds), 3) if self._bin_speeds else ""
        self._speed_w.writerow({"t_s": self._bin_t, "speed_ms": speed,
                                "ticks": self._bin_ticks, "lines": self._bin_lines})
        self._speed_f.flush()
        self._bin_speeds = []
        self._bin_ticks = 0
        self._bin_lines = 0

    def close(self, document: dict) -> None:
        if self.closed:
            return
        self.closed = True
        self._flush_second()
        for f in (self._row_f, self._sample_f, self._speed_f, self._stroke_f):
            f.close()
        (self.folder / "session.json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
