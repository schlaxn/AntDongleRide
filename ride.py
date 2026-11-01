"""Ride logic.

RideState  - pure state machine: workout clock, distance, altitude, latest sensor values.
             No threads, no I/O; fully unit-tested with fake timestamps.
RideEngine - background thread: consumes sensor samples from AntSession, advances
             RideState at 10 Hz, writes recordings, publishes "ride" ticks to the UI.
"""
import queue
import statistics
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from bots import Traffic
from gear import GEAR_RATIOS, GEARS, MAX_RATIO, MIN_RATIO, START_GEAR, cadence_for, choose_gear, gear_ratio
from physics import RoadPhysics
from power import TrainerPowerModel
from recorder import Recorder
from sensors import LIVE_S, SensorRoles
from workout import Workout


def _r(x, nd: int = 0):
    """Round for display; a missing sensor (None) stays None so the UI shows a dash."""
    return None if x is None else round(x, nd)

STALE_S = 2.5  # a sensor value older than this counts as zero (coasting / sensor silent)
HOLD_S = 3.0   # a speed gap up to this long is bridged by holding the last speed (speed reports come per wheel revolution)
METRICS = ("heart_rate", "cadence", "speed")


NO_INPUT_STOP_S = 30.0   # the bike stops when neither speed nor cadence has been sent for this long


class RideState:
    def __init__(self, workout: Workout, wheel_m: float = 2.105):
        self.workout = workout
        self.wheel_m = wheel_m
        self._gears: deque = deque(maxlen=200)  # ~20 s of live gear estimates
        self.segments = workout.segments()
        self.gradients = workout.gradient_per_second()
        self.status = "idle"  # idle | running | paused | finished
        self.elapsed = 0.0
        self.distance_m = 0.0
        self.altitude_m = 0.0
        self.power = TrainerPowerModel()
        self.power_w = 0.0
        self._prev_v: float | None = None
        self.phys = RoadPhysics()
        self.traffic: Traffic | None = None   # other riders, created when the ride starts
        self.v_virtual = 0.0        # m/s on the virtual road
        self.virtual_dist = 0.0     # m on the virtual road
        self.vgear = START_GEAR     # virtual gearing index (0 = gear 1, easiest)
        self.virtual_cadence = None  # rpm from virtual gearing; None when a real cadence sensor is in use
        self.cadence_rpm_now: float | None = None  # real cadence this tick (0 when a combined sensor is quiet)
        self.latest: dict[str, tuple[float, float]] = {}
        self._last_input: float | None = None   # wall time of the last speed or cadence sample

    # ----- sensor input -----
    def update(self, metric: str, ts: float, value: float) -> None:
        self.latest[metric] = (ts, value)
        if metric in ("speed", "cadence"):
            self._last_input = ts

    def value(self, metric: str, now: float) -> float | None:
        """Latest value, or None if the sensor has never sent it or it is stale."""
        item = self.latest.get(metric)
        if item is None:
            return None
        ts, val = item
        return val if now - ts <= STALE_S else None

    def gear_live(self) -> float | None:
        if not self._gears:
            return None
        return round(statistics.median(self._gears), 2)

    # ----- control -----
    def start(self) -> None:
        if self.status != "idle":
            raise RuntimeError(f"Cannot start from status {self.status}")
        self.status = "running"
        self.traffic = Traffic(self.workout.total_s)   # the other riders for this ride

    def pause(self) -> None:
        if self.status != "running":
            raise RuntimeError("Can only pause a running ride")
        self.status = "paused"

    def resume(self) -> None:
        if self.status != "paused":
            raise RuntimeError("Can only resume a paused ride")
        self.status = "running"

    def stop(self) -> None:
        if self.status in ("running", "paused"):
            self.status = "finished"

    # ----- time -----
    def advance(self, now: float, dt: float, combined_live: bool = False) -> None:
        """Move the ride forward by dt seconds of wall time (no-op unless running)."""
        if self.status not in ("running", "preview") or dt <= 0:
            return
        item = self.latest.get("speed")
        # hold the last speed through a short gap (up to HOLD_S), otherwise the speed counts as zero
        speed_kmh = item[1] if item is not None and now - item[0] <= HOLD_S else 0.0
        v = speed_kmh / 3.6
        accel = 0.0 if self._prev_v is None else (v - self._prev_v) / dt
        self._prev_v = v
        self.power_w = round(self.power.power_w(speed_kmh, accel), 1) if speed_kmh > 0 else 0.0
        # virtual speed: the power the trainer absorbs at the measured speed drives the virtual road
        p_rider = self.power.resistance_w(speed_kmh)
        grad = self.gradient_at(self.elapsed) if self.status == "running" else 0.0   # preview: flat course
        if self._last_input is None or now - self._last_input >= NO_INPUT_STOP_S:
            self.v_virtual = 0.0        # no speed or cadence input for 30 s: the bike stops
        else:
            self.v_virtual = self.phys.step(self.v_virtual, p_rider, grad, dt)
        self.virtual_dist += self.v_virtual * dt
        # A combined speed & cadence sensor that is still transmitting is a real cadence source:
        # when it reports no cadence (the crank has stopped) the cadence is 0, not virtual.
        cad_raw = self.value("cadence", now)
        if cad_raw is not None:
            cad = cad_raw
        elif combined_live:
            cad = 0.0
        else:
            cad = None
        self.cadence_rpm_now = cad
        # virtual gearing: without any cadence source, shift to keep cadence within 85-100 rpm
        if cad is None:
            self.vgear = choose_gear(self.v_virtual, self.vgear, self.wheel_m)
            self.virtual_cadence = round(cadence_for(self.v_virtual, GEAR_RATIOS[self.vgear], self.wheel_m), 1)
        else:
            self.virtual_cadence = None
        # gear needs cadence; without it the gear stays unknown
        g = gear_ratio(speed_kmh, cad, self.wheel_m) if cad else None
        if g is not None and MIN_RATIO <= g <= MAX_RATIO:
            self._gears.append(g)
        if self.status != "running":
            return   # preview: no distance, elapsed time or workout end
        self.distance_m += v * dt
        self.altitude_m += v * dt * self.gradient_at(self.elapsed) / 100.0
        self.elapsed += dt
        if self.traffic is not None:
            seg = self.phase()
            self.traffic.update(dt, self.elapsed, self.virtual_dist, self.v_virtual, self.power_w,
                                seg.kind if seg else None, self.gradient_at(self.elapsed))
        if self.elapsed >= self.workout.total_s:
            self.elapsed = float(self.workout.total_s)
            self.status = "finished"

    def gradient_at(self, t: float) -> float:
        i = int(t)
        return self.gradients[i] if 0 <= i < len(self.gradients) else 0.0

    # ----- output -----
    def phase(self):
        if self.status not in ("running", "paused"):
            return None
        return self.workout.phase_at(self.elapsed)

    def snapshot(self, now: float) -> dict:
        seg = self.phase()
        if seg is not None:
            idx = self.segments.index(seg)
            next_kind = self.segments[idx + 1].kind if idx + 1 < len(self.segments) else "finished"
            phase, block, rep = seg.kind, seg.block, seg.rep
            left = round(seg.end - self.elapsed, 1)
        else:
            next_kind = self.segments[0].kind if self.status == "idle" and self.segments else None
            phase, block, rep, left = self.status, 0, 0, 0.0
        return {
            "type": "ride",
            "status": self.status,
            "elapsed_s": round(self.elapsed, 1),
            "total_s": self.workout.total_s,
            "phase": phase,
            "block": block,
            "rep": rep,
            "phase_left_s": left,
            "next_phase": next_kind,
            "hr_bpm": _r(self.value("heart_rate", now)),
            "cadence_rpm": _r(self.cadence_rpm_now),
            "speed_kmh": _r(self.value("speed", now), 1),
            "power_w": _r(self.power_w),
            "virtual_speed_kmh": _r(self.v_virtual * 3.6, 1),
            "virtual_distance_m": _r(self.virtual_dist, 1),
            "virtual_cadence_rpm": _r(self.virtual_cadence, 1),
            "virtual_gear": (self.vgear + 1) if self.virtual_cadence is not None else None,
            "virtual_cogs": f"{GEARS[self.vgear][0]}/{GEARS[self.vgear][1]}" if self.virtual_cadence is not None else None,
            "virtual_gear_ratio": round(GEAR_RATIOS[self.vgear], 2),
            "gear_ratio": self.gear_live(),
            "development_m": _r((self.gear_live() or 0) * self.wheel_m or None, 2),
            "distance_m": round(self.distance_m, 1),
            "altitude_m": round(self.altitude_m, 1),
            "gradient_pct": self.gradient_at(self.elapsed),
            "bots": self.traffic.snapshot(self.virtual_dist)["riders"] if self.traffic else [],
            "bot_counts": dict(self.traffic.counts) if self.traffic else {"met": 0, "passed": 0, "passed_by": 0},
        }

    def row(self, now: float) -> dict:
        """One 1 Hz row for ride.csv."""
        snap = self.snapshot(now)
        return {
            "elapsed_s": int(self.elapsed),
            "iso_time": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "phase": snap["phase"],
            "block": snap["block"],
            "rep": snap["rep"],
            "hr_bpm": snap["hr_bpm"],
            "cadence_rpm": snap["cadence_rpm"],
            "speed_kmh": snap["speed_kmh"],
            "power_w": snap["power_w"],
            "virtual_speed_kmh": snap["virtual_speed_kmh"],
            "virtual_distance_m": snap["virtual_distance_m"],
            "virtual_cadence_rpm": snap["virtual_cadence_rpm"],
            "virtual_gear": snap["virtual_gear"],
            "gear_ratio": snap["gear_ratio"],
            "distance_m": snap["distance_m"],
            "gradient_pct": snap["gradient_pct"],
            "altitude_m": snap["altitude_m"],
        }


class RideEngine:
    def __init__(self, session, recordings_dir: Path | str, roles: SensorRoles | None = None,
                 tick_hz: float = 5.0):
        self.session = session
        self.roles = roles or SensorRoles()
        self.speed_multiplier = 1.0  # testing only: scales the speed the ride uses (e.g. x5 to meet the other riders quickly)
        self.recordings_dir = Path(recordings_dir)
        self.state: RideState | None = None
        self.recorder: Recorder | None = None
        self.events: list[dict] = []
        self.last_dir: Path | None = None
        self._lock = threading.RLock()
        self._q = session.subscribe()
        self._tick = 1.0 / tick_hz
        self._last_sec = -1
        self._finalized = False
        self._finished_at: float | None = None   # when a finished ride was first seen (preview returns after a pause)
        self._cadence_devices: set[int] = set()  # device numbers of combined sensors that have sent cadence
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ride-engine")
        self._thread.start()

    # ----- control (called from HTTP handlers) -----
    def load(self, workout: Workout) -> None:
        with self._lock:
            if self.state and self.state.status in ("running", "paused"):
                raise RuntimeError("Stop the current ride before loading a new workout")
            self.state = RideState(workout, self.session.decoder.wheel_m)
            self.events = []
            self._finalized = False

    def start(self) -> None:
        with self._lock:
            if self.state is None:
                raise RuntimeError("No workout loaded")
            now = time.time()
            if self.state.status == "preview":
                self.state.status = "idle"
            self.state.start()
            self.recorder = Recorder(self.recordings_dir, now)
            self.last_dir = self.recorder.dir
            self._last_sec = -1
            self._log("start", now)

    def pause(self) -> None:
        with self._lock:
            self._require_state().pause()
            self._log("pause", time.time())

    def resume(self) -> None:
        with self._lock:
            self._require_state().resume()
            self._log("resume", time.time())

    def stop(self) -> None:
        with self._lock:
            if self.state and self.state.status in ("running", "paused"):
                self.state.stop()
                self._log("stop", time.time())
                self._finish()

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.session.unsubscribe(self._q)

    # ----- internals -----
    def _require_state(self) -> RideState:
        if self.state is None:
            raise RuntimeError("No workout loaded")
        return self.state

    def _log(self, name: str, ts: float) -> None:
        self.events.append({"event": name, "ts": ts,
                            "elapsed_s": round(self.state.elapsed, 1) if self.state else 0})

    def _finish(self) -> None:
        if self._finalized or self.recorder is None or self.state is None:
            return
        self._finalized = True
        st = self.state
        doc = {
            "workout": st.workout.to_dict(),
            "events": self.events,
            "settings": {
                "wheel_circumference_m": self.session.decoder.wheel_m,
                "power_model": {"ref": [st.power.ref_speed_kmh, st.power.ref_power_w],
                                "top": [st.power.top_speed_kmh, st.power.top_power_w],
                                "exponent": round(st.power.exponent, 3),
                                "flywheel_kg": st.power.flywheel_kg},
            },
            "summary": {
                "duration_s": round(st.elapsed, 1),
                "distance_m": round(st.distance_m, 1),
                "altitude_gain_m": round(st.altitude_m, 1),
                "other_riders": dict(st.traffic.counts) if st.traffic else None,
            },
        }
        self.recorder.close(doc)
        self.recorder = None

    def _combined_live(self, now: float) -> bool:
        """True if a combined speed & cadence sensor (type 121) that feeds the speed or cadence
        role is transmitting, whether or not it currently reports a cadence."""
        wanted = [sel for role, sel in self.roles.selection.items()
                  if role in ("speed", "cadence") and sel["devtype"] == 121]
        if not wanted:
            return False
        for d in self.session.devices.values():
            # only a combined sensor that has sent cadence counts: a speed-only one never does
            if d["devtype"] != 121 or d["devnum"] not in self._cadence_devices:
                continue
            for sel in wanted:
                if sel["devnum"] in (0, d["devnum"]) and now - d.get("last", 0.0) <= LIVE_S:
                    return True
        return False

    def _loop(self) -> None:
        last = time.time()
        next_tick = 0.0
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.1)
            except queue.Empty:
                item = None
            with self._lock:
                while item is not None:
                    if item.get("type") == "sample":
                        if item.get("metric") == "cadence" and item.get("devtype") == 121:
                            self._cadence_devices.add(item.get("devnum"))
                        if self.state and item.get("metric") in METRICS and self.roles.accepts(item):
                            value = item["value"] * (self.speed_multiplier if item["metric"] == "speed" else 1.0)
                            self.state.update(item["metric"], item["ts"], value)
                        if self.recorder:
                            self.recorder.sample(item)
                    try:
                        item = self._q.get_nowait()
                    except queue.Empty:
                        item = None

                now = time.time()
                dt = now - last
                last = now
                # no ride recorded: run the physics as a live preview (sensors drive the bike)
                if self.state is None:
                    self.state = RideState(Workout(), self.session.decoder.wheel_m)
                # after a saved ride, show the final state for a few seconds, then go back to the live preview
                if self.state and self.state.status == "finished" and self.recorder is None:
                    if self._finished_at is None:
                        self._finished_at = now
                    elif now - self._finished_at > 5.0:
                        self.state = RideState(self.state.workout, self.session.decoder.wheel_m)
                        self._finished_at = None
                if self.state and self.state.status == "idle" and self.recorder is None:
                    self.state.status = "preview"
                if self.state and self.state.status in ("running", "preview"):
                    self.state.advance(now, dt, combined_live=self._combined_live(now))
                if self.state and self.state.status == "running":
                    sec = int(self.state.elapsed)
                    if sec != self._last_sec and self.recorder:
                        self._last_sec = sec
                        self.recorder.row(self.state.row(now))
                    if self.state.status == "finished":
                        self._log("finish", now)
                        self._finish()
                if self.state and now >= next_tick:
                    next_tick = now + self._tick
                    self.session.publish(self.state.snapshot(now))
