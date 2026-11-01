"""Writes one ride to recordings/<YYYY-MM-DD_HHMMSS>/.

    ride.csv     1 row per second: elapsed, time, phase, HR, cadence, speed, distance, gradient, altitude
    samples.csv  every raw decoded sample (for later re-analysis)
    session.json workout definition, start/pause/stop events, summary

Files are flushed on every write so a crash mid-ride keeps what was recorded.
"""
import csv
import json
import time
from pathlib import Path

RIDE_COLUMNS = [
    "elapsed_s", "iso_time", "phase", "block", "rep",
    "hr_bpm", "cadence_rpm", "speed_kmh", "power_w", "virtual_speed_kmh", "virtual_distance_m",
    "virtual_cadence_rpm", "virtual_gear",
    "gear_ratio", "distance_m", "gradient_pct", "altitude_m",
]
CLEAN_COLUMNS = RIDE_COLUMNS + ["cleaned"]  # gear_ratio is already a ride column
SAMPLE_COLUMNS = ["ts", "devtype", "devnum", "metric", "value", "unit"]


def _avg(values):
    return round(sum(values) / len(values), 2) if values else None


class Recorder:
    def __init__(self, root: Path | str, started_at: float):
        stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(started_at))
        self.dir = Path(root) / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ride_f = open(self.dir / "ride.csv", "w", newline="", encoding="utf-8")
        self._ride = csv.writer(self._ride_f)
        self._ride.writerow(RIDE_COLUMNS)
        self._sample_f = open(self.dir / "samples.csv", "w", newline="", encoding="utf-8")
        self._samples = csv.writer(self._sample_f)
        self._samples.writerow(SAMPLE_COLUMNS)
        self._hr, self._cad, self._spd = [], [], []
        self._all_rows: list[dict] = []
        self._rows = 0
        self.closed = False

    def row(self, r: dict) -> None:
        self._ride.writerow([r[c] for c in RIDE_COLUMNS])
        self._ride_f.flush()
        self._rows += 1
        self._all_rows.append(dict(r))
        if r["hr_bpm"]:
            self._hr.append(r["hr_bpm"])
        if r["cadence_rpm"]:
            self._cad.append(r["cadence_rpm"])
        if r["speed_kmh"]:
            self._spd.append(r["speed_kmh"])

    def sample(self, s: dict) -> None:
        self._samples.writerow([s["ts"], s["devtype"], s["devnum"], s["metric"], s["value"], s["unit"]])
        self._sample_f.flush()

    def summary(self) -> dict:
        return {
            "seconds_recorded": self._rows,
            "avg_hr_bpm": _avg(self._hr),
            "max_hr_bpm": max(self._hr) if self._hr else None,
            "avg_cadence_rpm": _avg(self._cad),
            "max_cadence_rpm": max(self._cad) if self._cad else None,
            "avg_speed_kmh": _avg(self._spd),
            "max_speed_kmh": max(self._spd) if self._spd else None,
        }

    def close(self, document: dict) -> None:
        if self.closed:
            return
        self.closed = True
        self._ride_f.close()
        self._sample_f.close()
        document = {**document, "summary": {**self.summary(), **document.get("summary", {})}}
        (self.dir / "session.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        wheel_m = document.get("settings", {}).get("wheel_circumference_m")
        if wheel_m and self._all_rows:
            from gear import clean_rows  # local import keeps recorder free of gear logic at import time
            cleaned = clean_rows(self._all_rows, wheel_m)
            with open(self.dir / "ride_clean.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(CLEAN_COLUMNS)
                for r in cleaned:
                    w.writerow([r.get(c, "") for c in CLEAN_COLUMNS])
