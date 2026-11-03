"""Calibration of the S4 speed scale, one fill level at a time (17 L is the first trial).

Demands come from the intended watt levels through the cube law: the speed that gives W
at this fill setting is v = (W / (WATT_CONST * fill_factor)) ** (1/3), shown to the rower as
a pace (s per 500 m) and in m/s. Each step locks when the raw pulse code is steady
(see the rule below). The reference speed for the fit is the monitor's own m/s reading,
entered when the step locks; until one is entered, the demanded speed stands in for it.

Steady-state rule (per step):
  - the step is shown at t0; nothing counts before t0 + MIN_SETTLE_S (30 s)
  - the signal is the mean code of each stroke cycle (SS to SS): one value per stroke
  - the step locks when the trailing run of those means stays within the band for HOLD_S (5 s)
    and that run reaches past the settle gate
  - the band is 2 % on power; power goes with v**3 and code with v, so 2 % power is 2/3 % code
"""
import csv
import json
import queue
import threading
import time
from collections import deque
from pathlib import Path

from .profiles import CODE_PER_MS, WATT_CONST, fill_factor

CAL_LEVELS = (15, 17, 19, 20)      # fill levels the monitor allows (15.0 to 20.0 L)
CAL_WATTS = (100, 60, 150)         # intended watt levels, in the order they are run
MIN_SETTLE_S = 30.0                # nothing counts before this long after a step is shown
MAX_STEP_S = 120.0                 # a step that never locks is offered for retry or skip
WINDOW_S = 3.0                     # the steadiness signal is a 3 s mean of the raw codes
EVAL_S = 1.0                       # ... evaluated once a second
HOLD_S = 5.0                       # ... and must stay in band this long (after the settle gate)
STEADY_POWER_PCT = 2.0             # band on power
CODE_BAND = (STEADY_POWER_PCT / 100.0) / 3.0   # relative band on code (power ~ v**3, code ~ v)
LOG_METRICS = ("code", "stroke", "stroke_rate", "speed", "power", "split_500m", "heart_rate")
OFF_PACE_PCT = 15.0                # a locked code this far from the nominal scale is flagged
STALE_CYCLE_S = 2.5                # no stroke for this long: the steady run no longer counts


def speed_for_watts(watts: float, fill_l: int = 17) -> float:
    """m/s that the cube law gives for these watts at this fill setting."""
    return (watts / (WATT_CONST * fill_factor(fill_l))) ** (1.0 / 3.0)


def demand_for_watts(watts: float, fill_l: int = 17) -> float:
    """The speed the rower is asked to hold: the cube-law speed rounded to 0.1 m/s, so the
    targets are smooth values to aim for."""
    return round(speed_for_watts(watts, fill_l), 1)


def watts_for_speed(v_ms: float, fill_l: int = 17) -> float:
    """Watts from speed with the cube law, W = 1.78 * v**3 (x fill factor), 1 decimal."""
    return round(WATT_CONST * fill_factor(fill_l) * v_ms ** 3, 1)


def pace_of_speed(v_ms: float) -> float:
    """Seconds per 500 m."""
    return 500.0 / v_ms


def fmt_pace(pace_s: float) -> str:
    m, s = divmod(int(round(pace_s)), 60)
    return f"{m}:{s:02d}"


def _judge(code: float, v_ref: float) -> tuple[float, bool]:
    """k = code per m/s at the reference speed, and whether it is far from the nominal scale."""
    k = code / v_ref
    off = abs(code / (CODE_PER_MS * v_ref) - 1.0) * 100.0 > OFF_PACE_PCT
    return k, off


class StepTracker:
    """Watches one demanded speed and locks it when the code is steady."""

    def __init__(self, v_target: float, t0: float, target_w: float):
        self.v_target = v_target
        self.target_w = target_w
        self.t0 = t0
        self._raw: deque = deque()          # (ts, code), kept for the last HOLD_S + WINDOW_S
        self.means: list[tuple[float, float]] = []   # (ts, stroke-cycle mean)
        self._cycle_start: float | None = None
        self.locked: dict | None = None
        self.timed_out = False
        self._steady_since: float | None = None      # start of the trailing in-band run

    def feed(self, ts: float, code: float) -> None:
        self._raw.append((ts, code))

    def feed_stroke(self, ts: float) -> None:
        """A stroke start (SS). The cycle that just ended gives one mean; the step can lock here."""
        if self.locked is not None or self.timed_out:
            return
        if self._cycle_start is not None and ts > self._cycle_start:
            cyc = [c for t, c in self._raw if self._cycle_start <= t < ts]
            if cyc:
                self.means.append((ts, sum(cyc) / len(cyc)))
                self._steady_since = self._trailing_run_start()
                if self._steady_since is not None and                         ts - max(self._steady_since, self.t0 + MIN_SETTLE_S) >= HOLD_S:
                    self._lock(ts)
        self._cycle_start = ts

    def tick(self, now: float) -> None:
        if self.locked is not None or self.timed_out:
            return
        while self._raw and self._raw[0][0] < now - 10.0:
            self._raw.popleft()
        if now - self.t0 >= MAX_STEP_S:
            self.timed_out = True
            return
        # the lock is also checked between strokes, while the last cycle is recent
        recent = bool(self.means) and now - self.means[-1][0] <= STALE_CYCLE_S
        if self._steady_since is not None and recent and now - max(self._steady_since, self.t0 + MIN_SETTLE_S) >= HOLD_S:
            self._lock(now)

    def _trailing_run_start(self) -> float | None:
        """Earliest time from which every mean stays inside the band."""
        if not self.means:
            return None
        lo = hi = self.means[-1][1]
        start = self.means[-1][0]
        for t, m in reversed(self.means):
            nlo, nhi = min(lo, m), max(hi, m)
            mid = (nlo + nhi) / 2.0
            if mid <= 0 or (nhi - nlo) / mid > CODE_BAND:
                break
            lo, hi, start = nlo, nhi, t
        return start

    def steady_s(self, now: float) -> float:
        """Seconds the signal has been steady, counted from the settle gate."""
        if self._steady_since is None or not self.means or now - self.means[-1][0] > STALE_CYCLE_S:
            return 0.0
        return max(0.0, now - max(self._steady_since, self.t0 + MIN_SETTLE_S))

    def _lock(self, now: float) -> None:
        window = [c for t, c in self._raw if t > now - HOLD_S]
        code = sum(window) / len(window)
        k, off = _judge(code, self.v_target)
        self.locked = {
            "target_w": round(self.target_w, 1),
            "target_ms": round(self.v_target, 4),
            "pace": fmt_pace(pace_of_speed(self.v_target)),
            "code": round(code, 3),
            "monitor_ms": None,              # the monitor's m/s reading, entered by the user
            "v_ref": round(self.v_target, 4),
            "k_code_per_ms": round(k, 4),
            "settle_s": round(self._steady_since - self.t0, 1),
            "lock_s": round(now - self.t0, 1),
            "off_pace": off,
        }

    def trace(self, now: float, keep_s: float = 240.0) -> list[list[float]]:
        """[elapsed_s, mean] pairs for the page's trace."""
        return [[round(t - self.t0, 1), round(m, 3)] for t, m in self.means if now - t <= keep_s]


class CalSession:
    """Runs the steps of one fill level in order: one per intended watt level."""

    def __init__(self, recordings_dir: Path | None = None, watts=CAL_WATTS):
        self.recordings_dir = Path(recordings_dir) if recordings_dir else None
        self.watts = tuple(watts)
        self.fill_l: int | None = None
        self.status = "idle"            # idle | running | done | aborted
        self.index = 0
        self.tracker: StepTracker | None = None
        self.results: list[dict] = []   # one per locked step, plus skipped steps
        self.trace: list[tuple[float, float, int]] = []   # (ts, code, step) for the csv
        self.log: list[tuple[float, str, float]] = []     # every rower sample from start to end
        self.started_at: float | None = None
        self.saved_to: str | None = None
        self._last_code_ts: float | None = None

    # ---------- control ----------
    def start(self, fill_l: int, now: float) -> None:
        if fill_l not in CAL_LEVELS:
            raise ValueError(f"Fill level {fill_l} L is not calibrated yet")
        if self.status == "running":
            raise RuntimeError("A calibration is already running")
        self.__init__(self.recordings_dir, self.watts)
        self.fill_l = fill_l
        self.status = "running"
        self.started_at = now
        self._begin(now)

    def _begin(self, now: float) -> None:
        fill = self.fill_l or 17
        w = self.watts[self.index]
        v = demand_for_watts(w, fill)          # the speed for these watts, to 0.1 m/s
        self.tracker = StepTracker(v, now, w)

    def retry(self, now: float) -> None:
        self._require_running()
        self._begin(now)

    def skip(self, now: float) -> None:
        self._require_running()
        cur = self.tracker
        self.results.append({"step": self.index + 1, "target_w": cur.target_w,
                             "target_ms": round(cur.v_target, 4), "skipped": True})
        self._advance(now)

    def redo_previous(self, now: float) -> None:
        self._require_running()
        if self.index == 0:
            raise RuntimeError("There is no previous step")
        self.results = [r for r in self.results if r["step"] != self.index]
        self.index -= 1
        self._begin(now)

    def set_reading(self, speed_ms: float, now: float) -> None:
        """The monitor's m/s for the most recent locked step: it becomes the fit's reference."""
        if not self.results or self.results[-1].get("skipped"):
            raise RuntimeError("No locked step to read the monitor for")
        if not (0.5 < speed_ms < 10.0):
            raise ValueError("Monitor speed must be between 0.5 and 10 m/s")
        res = self.results[-1]
        k, off = _judge(res["code"], speed_ms)
        res.update({"monitor_ms": round(speed_ms, 3), "v_ref": round(speed_ms, 4),
                    "k_code_per_ms": round(k, 4), "off_pace": off})
        if self.saved_to:
            self._save(now)

    def abort(self, now: float) -> None:
        if self.status != "running":
            return
        self.status = "aborted"
        self.tracker = None
        self._save(now)

    # ---------- inputs ----------
    def log_sample(self, item: dict) -> None:
        """Every rower sample (codes, strokes, speed, power) from Start until the calibration ends."""
        if self.status == "running" and item.get("metric") in LOG_METRICS:
            self.log.append((item["ts"], item["metric"], item["value"]))

    def feed(self, ts: float, code: float) -> None:
        if self.status != "running" or self.tracker is None:
            return
        self._last_code_ts = ts
        self.trace.append((ts, code, self.index + 1))
        self.tracker.feed(ts, code)

    def feed_stroke(self, ts: float) -> None:
        if self.status != "running" or self.tracker is None:
            return
        self.tracker.feed_stroke(ts)
        self._collect(ts)

    def tick(self, now: float) -> None:
        if self.status != "running" or self.tracker is None:
            return
        self.tracker.tick(now)
        self._collect(now)

    def _collect(self, now: float) -> None:
        if self.tracker.locked is not None:
            self.results.append({"step": self.index + 1, **self.tracker.locked})
            self._advance(now)

    def demand_speed(self) -> float | None:
        """Speed the rower is asked to hold now (for the simulator); None when no step is due."""
        if self.status != "running" or self.tracker is None or self.tracker.locked is not None:
            return None
        return self.tracker.v_target

    # ---------- helpers ----------
    def _require_running(self) -> None:
        if self.status != "running" or self.tracker is None:
            raise RuntimeError("No calibration is running")

    def _advance(self, now: float) -> None:
        self.index += 1
        if self.index >= len(self.watts):
            self.status = "done"
            self.tracker = None
            self._save(now)
        else:
            self._begin(now)

    def fit(self) -> dict | None:
        """Least squares through the origin: code = k * v over the locked steps of this level."""
        steps = [r for r in self.results if not r.get("skipped")]
        if not steps:
            return None
        k = sum(r["code"] * r["v_ref"] for r in steps) / sum(r["v_ref"] ** 2 for r in steps)
        all_read = all(r.get("monitor_ms") is not None for r in steps)
        return {"fill_l": self.fill_l, "code_per_ms": round(k, 4), "points": len(steps),
                "nominal_code_per_ms": CODE_PER_MS,
                "deviation_pct": round((k / CODE_PER_MS - 1.0) * 100.0, 2),
                "reference": "monitor" if all_read else "demanded (monitor reading missing for some steps)"}

    def snapshot(self, now: float) -> dict:
        cur = self.tracker
        last = self.results[-1] if self.results else None
        d = {
            "type": "cal",
            "status": self.status,
            "fill_l": self.fill_l,
            "levels": list(CAL_LEVELS),
            "steps": len(self.watts),
            "targets_w": list(self.watts),
            "step": self.index + 1 if self.status == "running" else None,
            "gate_s": MIN_SETTLE_S,
            "max_step_s": MAX_STEP_S,
            "hold_s": HOLD_S,
            "band_pct": round(CODE_BAND * 100.0, 3),
            "results": self.results,
            "awaiting_reading": bool(last and not last.get("skipped") and last.get("monitor_ms") is None),
            "fit": self.fit(),
            "saved_to": self.saved_to,
            "code_age_s": None if self._last_code_ts is None else round(now - self._last_code_ts, 1),
        }
        if cur is not None:
            last_mean = cur.means[-1][1] if cur.means else None
            d.update({
                "pace": fmt_pace(pace_of_speed(cur.v_target)),
                "target_ms": round(cur.v_target, 3),
                "expected_w": round(cur.target_w, 1),
                "elapsed_s": round(now - cur.t0, 1),
                "settling": now - cur.t0 < MIN_SETTLE_S,
                "steady_s": round(cur.steady_s(now), 1),
                "timed_out": cur.timed_out,
                "mean_code": None if last_mean is None else round(last_mean, 3),
                "trace": cur.trace(now),
            })
        return d

    def _save(self, now: float) -> None:
        if self.recordings_dir is None or (not self.trace and not self.results and not self.log):
            return
        stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(self.started_at or now))
        folder = self.recordings_dir / f"cal_{stamp}_{self.fill_l}L"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "trace.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts", "code", "step"])
            w.writerows(self.trace)
        with open(folder / "samples.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts", "metric", "value"])
            w.writerows(self.log)
        doc = {"status": self.status, "fill_l": self.fill_l, "started_at": self.started_at,
               "targets_w": list(self.watts), "results": self.results, "fit": self.fit(),
               "samples_logged": len(self.log),
               "settings": {"min_settle_s": MIN_SETTLE_S, "max_step_s": MAX_STEP_S,
                            "window_s": WINDOW_S, "hold_s": HOLD_S,
                            "steady_power_pct": STEADY_POWER_PCT, "code_band": CODE_BAND}}
        (folder / "cal.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
        self.saved_to = str(folder)


class CalRunner:
    """Owns a CalSession and feeds it the code samples from the hub, on its own thread."""

    def __init__(self, hub, roles, recordings_dir: Path, tick_hz: float = 2.0):
        self.hub = hub
        self.roles = roles
        self.tick_hz = tick_hz
        self._lock = threading.RLock()
        self._q = hub.subscribe()
        self._stop = threading.Event()
        self.session = CalSession(recordings_dir)
        self.last_code_ts: float | None = None     # monitor status, independent of the session
        self.last_code: float | None = None
        self.strokes_total = 0
        self._code_ts: deque = deque(maxlen=400)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="row-cal")
        self._thread.start()

    def _loop(self) -> None:
        last_pub = 0.0
        while not self._stop.is_set():
            items = []
            try:
                items.append(self._q.get(timeout=0.1))
            except queue.Empty:
                pass
            while True:
                try:
                    items.append(self._q.get_nowait())
                except queue.Empty:
                    break
            now = time.time()
            with self._lock:
                for item in items:
                    if item.get("type") != "sample" or not self.roles.accepts(item):
                        continue
                    self.session.log_sample(item)
                    if item.get("metric") == "code":
                        self.last_code_ts, self.last_code = item["ts"], item["value"]
                        self._code_ts.append(item["ts"])
                        self.session.feed(item["ts"], item["value"])
                    elif item.get("metric") == "stroke":
                        self.strokes_total += 1
                        self.session.feed_stroke(item["ts"])
                self.session.tick(now)
                if now - last_pub >= 1.0 / self.tick_hz:
                    last_pub = now
                    self.hub.publish(self._snap(now))

    # ---------- control (called from the web routes) ----------
    def _snap(self, now: float) -> dict:
        """The session snapshot plus the monitor status (valid whatever the session state)."""
        d = self.session.snapshot(now)
        age = None if self.last_code_ts is None else round(now - self.last_code_ts, 1)
        rate = sum(1 for t in self._code_ts if now - t <= 2.0) / 2.0
        d["monitor"] = {
            "live": age is not None and age <= 2.0,
            "age_s": age,
            "last_code": self.last_code,
            "lines_per_s": round(rate, 1),
            "strokes": self.strokes_total,
        }
        return d

    def _call(self, fn, *args):
        with self._lock:
            fn(*args, time.time())
            snap = self._snap(time.time())
        self.hub.publish(snap)
        return snap

    def state(self) -> dict:
        with self._lock:
            return self._snap(time.time())

    def start(self, fill_l: int) -> dict:
        return self._call(self.session.start, fill_l)

    def retry(self) -> dict:
        return self._call(self.session.retry)

    def skip(self) -> dict:
        return self._call(self.session.skip)

    def redo_previous(self) -> dict:
        return self._call(self.session.redo_previous)

    def reading(self, speed_ms: float) -> dict:
        return self._call(self.session.set_reading, speed_ms)

    def abort(self) -> dict:
        return self._call(self.session.abort)

    def demand_speed(self) -> float | None:
        with self._lock:
            return self.session.demand_speed()

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.hub.unsubscribe(self._q)
