"""Simulated rower: drives the whole app without hardware.

Speed follows the current phase's target watts through the cube law, stroke events come
at a rate that rises with effort, and heart rate follows effort with a lag. Samples go
into the hub exactly as real ones would, so the engine and UI cannot tell the difference.
"""
import random
import threading
import time

from .profiles import ROWER_DEVNUM, ROWER_DEVTYPE, WATT_CONST, Sample

BLE_HR_DEVTYPE = 120
BLE_HR_DEVNUM = -1


class SimulatedRower:
    def __init__(self, hub, engine, seed: int = 1, tick_s: float = 0.1):
        self.hub = hub
        self.engine = engine
        self.rng = random.Random(seed)
        self.tick_s = tick_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hr = 70.0
        self._next_stroke: float | None = None
        self._last_hr_ts = 0.0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="row-sim")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _effort_w(self) -> float:
        """Target watts for the current phase, or 0 when not rowing."""
        with self.engine._lock:
            st = self.engine.state
            if st is None or st.status not in ("running", "preview"):
                return 0.0
            seg = st.workout.phase_at(st.elapsed) if st.status == "running" else None
            kind = seg.kind if seg else "warmup"
            return float(st.targets.get(kind, 0))

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            watts = self._effort_w()
            v = (watts / WATT_CONST) ** (1.0 / 3.0) if watts > 0 else 0.0
            if v > 0:
                v *= 1.0 + self.rng.uniform(-0.03, 0.03)
            self.hub.ingest_sample(Sample(now, "speed", round(v, 3), "m/s",
                                          devtype=ROWER_DEVTYPE, devnum=ROWER_DEVNUM))

            spm = 0.0 if watts <= 0 else 22.0 + 0.07 * watts
            if spm > 0:
                if self._next_stroke is None:
                    self._next_stroke = now
                if now >= self._next_stroke:
                    self.hub.ingest_sample(Sample(now, "stroke", 1, "count",
                                                  devtype=ROWER_DEVTYPE, devnum=ROWER_DEVNUM))
                    self.hub.ingest_sample(Sample(now, "stroke_rate", round(spm, 1), "spm",
                                                  devtype=ROWER_DEVTYPE, devnum=ROWER_DEVNUM))
                    self._next_stroke = now + 60.0 / spm
            else:
                self._next_stroke = None

            if now - self._last_hr_ts >= 1.0:
                self._last_hr_ts = now
                target = 80.0 + 0.3 * watts
                self._hr += (target - self._hr) * 0.15 + self.rng.uniform(-1.0, 1.0)
                self.hub.ingest_sample(Sample(now, "heart_rate", int(round(self._hr)), "bpm",
                                              devtype=BLE_HR_DEVTYPE, devnum=BLE_HR_DEVNUM))
            time.sleep(self.tick_s)

