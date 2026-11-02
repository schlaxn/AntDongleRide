"""Rowing engine: the rower twin of ride.py.

RowState is pure (no threads, no I/O). RowEngine owns the thread, the lock, the recorder
and the 5 Hz snapshot publisher. Unlike RideEngine, a finished session is always
finalized once: either by stop() or by the loop noticing the clock ran out.
"""
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from workout import Segment, Workout, parse_shorthand

from bots import Traffic

from .boat import BoatPhysics
from .profiles import WATT_CONST
from .recorder import RowRecorder


class BoatBotAdapter:
    """Lets the ride app's bot traffic run on boat physics: same step(v, power, grade, dt) call."""

    def __init__(self, boat: BoatPhysics):
        self.boat = boat

    def step(self, v, power_w, grade, dt):
        return self.boat.step(v, power_w, dt)

STALE_S = 2.5          # a value older than this is shown as absent
HOLD_S = 3.0           # speed gap up to this long holds the last value for physics
STROKE_HOLD_S = 8.0    # the stroke rate is a rate; hold it longer than the speed
METRICS = ("speed", "stroke", "stroke_rate", "power", "heart_rate")

# Target watts per phase kind. Sustained efforts stay at or below 180 W.
DEFAULT_TARGETS = {"warmup": 90, "work": 150, "recover": 70, "between": 50, "cooldown": 80}
DEFAULT_SHORTHAND = "2-5-60-60"


def _r(x, nd=0):
    return None if x is None else round(x, nd)


class RowState:
    def __init__(self, workout: Workout, boat: BoatPhysics | None = None,
                 targets: dict | None = None):
        self.workout = workout
        self.boat = boat or BoatPhysics()
        self.targets = dict(DEFAULT_TARGETS if targets is None else targets)
        self.segments: list[Segment] = workout.segments()
        self.total_s = workout.total_s
        self.status = "preview"     # live physics on the flat river before Start
        self.elapsed = 0.0
        self.distance_m = 0.0
        self.virtual_m = 0.0
        self.v_virtual = 0.0
        self.strokes = 0
        self.latest: dict[str, tuple[float, float]] = {}
        self.power_w = 0.0
        self._energy_j = 0.0
        self._run_s = 0.0
        self.bots_on = True
        self.traffic: Traffic | None = None   # other boats (dolphins), created on start

    # ---------- inputs ----------
    def update(self, metric: str, ts: float, value: float) -> None:
        if metric == "stroke":
            self.strokes += 1
        self.latest[metric] = (ts, value)

    def value(self, metric: str, now: float, hold: float = HOLD_S):
        item = self.latest.get(metric)
        if not item:
            return None
        ts, v = item
        if now - ts > hold:
            return None
        return v

    # ---------- control ----------
    def start(self) -> None:
        if self.status not in ("idle", "preview"):
            raise RuntimeError(f"Cannot start from {self.status}")
        self.status = "running"

    def pause(self) -> None:
        if self.status != "running":
            raise RuntimeError(f"Cannot pause from {self.status}")
        self.status = "paused"

    def resume(self) -> None:
        if self.status != "paused":
            raise RuntimeError(f"Cannot resume from {self.status}")
        self.status = "running"

    def stop(self) -> None:
        if self.status in ("running", "paused"):
            self.status = "finished"

    # ---------- time ----------
    def phase(self) -> Segment | None:
        if self.status not in ("running", "paused"):
            return None
        return self.workout.phase_at(self.elapsed)

    def advance(self, now: float, dt: float) -> None:
        """Integrate boat physics. The clock and distances only move while running."""
        if dt <= 0 or self.status not in ("running", "preview"):
            return
        v_in = self.value("speed", now) or 0.0
        p_in = WATT_CONST * max(v_in, 0.0) ** 3
        self.power_w = p_in
        self.v_virtual = self.boat.step(self.v_virtual, p_in, dt)
        if self.status == "running":
            self.distance_m += v_in * dt
            self.virtual_m += self.v_virtual * dt
            self.elapsed += dt
            self._run_s += dt
            self._energy_j += p_in * dt
            if self.elapsed >= self.total_s:
                self.elapsed = float(self.total_s)
                self.status = "finished"

    # ---------- output ----------
    def _next_kind(self, seg: Segment | None) -> str:
        if not self.segments:
            return "finished"
        if seg is None:
            return self.segments[0].kind
        for i, s in enumerate(self.segments):
            if s.start == seg.start and s.kind == seg.kind:
                return self.segments[i + 1].kind if i + 1 < len(self.segments) else "finished"
        return "finished"

    def snapshot(self, now: float) -> dict:
        seg = self.phase()
        kind = seg.kind if seg else self.status
        v_sensor = self.value("speed", now)
        spm = self.value("stroke_rate", now, hold=STROKE_HOLD_S)
        power = self.value("power", now)
        avg_power = self._energy_j / self._run_s if self._run_s > 0 else None
        return {
            "type": "row",
            "status": self.status,
            "elapsed_s": _r(self.elapsed, 1),
            "total_s": self.total_s,
            "phase": kind,
            "block": seg.block if seg else 0,
            "rep": seg.rep if seg else 0,
            "phase_left_s": _r(seg.end - self.elapsed, 1) if seg else None,
            "next_phase": self._next_kind(seg),
            "speed_ms": _r(v_sensor, 2),
            "split_s": _r(500.0 / v_sensor, 1) if v_sensor and v_sensor > 0 else None,
            "power_w": _r(power if power is not None else self.power_w),
            "avg_power_w": _r(avg_power),
            "stroke_rate_spm": _r(spm, 1),
            "strokes": self.strokes,
            "distance_per_stroke_m": _r(self.distance_m / self.strokes, 2) if self.strokes else None,
            "distance_m": _r(self.distance_m, 1),
            "virtual_speed_ms": _r(self.v_virtual, 2),
            "virtual_distance_m": _r(self.virtual_m, 1),
            "hr_bpm": self.value("heart_rate", now, hold=STALE_S),
            "target_w": self.targets.get(kind),
            # dolphins are shown without names: only position, lane and speed are sent
            "bots": [{k: v for k, v in r.items() if k not in ("name", "arch")}
                     for r in self.traffic.snapshot(self.virtual_m)["riders"]] if self.traffic else [],
            "bot_counts": dict(self.traffic.counts) if self.traffic else {"met": 0, "passed": 0, "passed_by": 0},
        }


class RowEngine:
    def __init__(self, hub, recordings_dir: Path, roles, workout: Workout | None = None,
                 targets: dict | None = None, tick_hz: float = 5.0):
        self.hub = hub
        self.roles = roles
        self.recordings_dir = Path(recordings_dir)
        self.tick_hz = tick_hz
        self._lock = threading.RLock()
        self._q = hub.subscribe()
        self._stop = threading.Event()
        self._finalized = True
        self._last_sec = -1
        self.events: list[dict] = []
        self.recorder: RowRecorder | None = None
        self.state: RowState | None = None
        self.load(workout or parse_shorthand(DEFAULT_SHORTHAND, warmup_s=300, cooldown_s=300,
                                             between_s=120), targets)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="row-engine")
        self._thread.start()

    # ---------- control ----------
    def load(self, workout: Workout, targets: dict | None = None) -> None:
        with self._lock:
            if self.state and self.state.status in ("running", "paused"):
                raise RuntimeError("Stop the session before loading a new workout")
            self.state = RowState(workout, targets=targets)
            self.events = []

    def _start_bots(self) -> None:
        st = self.state
        st.traffic = None
        if st.bots_on:
            st.traffic = Traffic(st.total_s)
            st.traffic.physics = BoatBotAdapter(st.boat)

    def set_bots(self, on: bool) -> None:
        with self._lock:
            self.state.bots_on = bool(on)
            if not on:
                self.state.traffic = None
            elif self.state.status == "running" and self.state.traffic is None:
                self._start_bots()

    def _log(self, name: str) -> None:
        ev = {"event": name, "ts": time.time(), "elapsed_s": _r(self.state.elapsed, 1)}
        self.events.append(ev)
        self.hub.event(f"row {name}")

    def start(self) -> None:
        with self._lock:
            if self.state is None:
                raise RuntimeError("No workout loaded")
            if self.state.status == "finished":
                self.load(self.state.workout, self.state.targets)
            self.state.start()
            self._start_bots()
            now = time.time()
            self.recorder = RowRecorder(self.recordings_dir, now)
            self._finalized = False
            self._last_sec = -1
            self._log("start")

    def pause(self) -> None:
        with self._lock:
            self.state.pause()
            self._log("pause")

    def resume(self) -> None:
        with self._lock:
            self.state.resume()
            self._log("resume")

    def stop(self) -> None:
        with self._lock:
            if self.state.status not in ("running", "paused"):
                return
            self.state.stop()
            self._log("stop")
            self._finish()

    def shutdown(self) -> None:
        """Stop the loop. An active session is stopped and saved first."""
        with self._lock:
            if self.state is not None and self.state.status in ("running", "paused"):
                self.state.stop()
                self._log("stop")
                self._finish()
        self._stop.set()
        self._thread.join(timeout=2)
        self.hub.unsubscribe(self._q)

    # ---------- finalize ----------
    def _finish(self) -> None:
        """Write session.json and close the recorder. Runs once per session."""
        if self._finalized:
            return
        self._finalized = True
        st = self.state
        doc = {
            "workout": st.workout.to_dict(),
            "events": self.events,
            "boat": st.boat.params(),
            "targets": st.targets,
            "summary": {
                "duration_s": _r(st.elapsed, 1),
                "distance_m": _r(st.distance_m, 1),
                "virtual_distance_m": _r(st.virtual_m, 1),
                "strokes": st.strokes,
                "avg_power_w": _r(st._energy_j / st._run_s, 1) if st._run_s > 0 else None,
            },
        }
        if self.recorder is not None:
            self.recorder.close(doc)
            self.recorder = None

    # ---------- loop ----------
    def _loop(self) -> None:
        last = time.time()
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
            dt = now - last
            last = now
            with self._lock:
                st = self.state
                for item in items:
                    if item.get("type") != "sample":
                        continue
                    if self.recorder is not None:
                        self.recorder.sample(item)
                    if item.get("metric") in METRICS and self.roles.accepts(item):
                        st.update(item["metric"], item["ts"], item["value"])
                st.advance(now, dt)

                if st.status == "running" and st.traffic is not None:
                    ph = st.phase()
                    st.traffic.update(dt, st.elapsed, st.virtual_m, st.v_virtual, st.power_w,
                                      ph.kind if ph else None, 0.0)

                if st.status == "running":
                    sec = int(st.elapsed)
                    if sec != self._last_sec and self.recorder is not None:
                        self._last_sec = sec
                        snap = st.snapshot(now)
                        snap["iso_time"] = datetime.fromtimestamp(now).isoformat(timespec="seconds")
                        self.recorder.row(snap)

                if st.status == "finished" and not self._finalized:
                    self._log("finish")
                    self._finish()

                if now - last_pub >= 1.0 / self.tick_hz:
                    last_pub = now
                    self.hub.publish(st.snapshot(now))
