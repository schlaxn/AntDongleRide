"""Simulated rider for testing the ride page without a bike or sensors.

    python app.py --simulate

Every second it injects heart rate, cadence and speed into the same stream the real
sensors use, shaped by the current workout phase (faster and harder on work intervals).
"""
import random
import threading
import time

# (speed km/h, cadence rpm, heart-rate target bpm) per phase kind
PROFILE = {
    "warmup": (24.0, 85, 115),
    "work": (32.0, 98, 165),
    "recover": (22.0, 70, 135),
    "between": (22.0, 80, 125),
    "cooldown": (20.0, 70, 110),
}
IDLE = (0.0, 0, 70)


class SimulatedRider:
    def __init__(self, session, engine, cadence: bool = True):
        self.session = session
        self.engine = engine
        self.cadence = cadence  # False simulates a speed + heart-rate setup with no cadence sensor
        self._stop = threading.Event()
        self._hr = 70.0
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sim-rider")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            kind = None
            if self.engine.state is not None:
                seg = self.engine.state.phase()
                kind = seg.kind if seg else None
            speed, cadence, hr_target = PROFILE.get(kind, IDLE)
            if self.engine.state is not None and self.engine.state.status == "paused":
                speed, cadence = 0.0, 0
            # Heart rate follows the target with a lag, like a real body.
            self._hr += (hr_target - self._hr) * 0.15 + random.uniform(-1.0, 1.0)
            # simulated device numbers: heart-rate strap 500, speed/cadence sensor 1200
            self.session.ingest_sample(now, 120, 500, "heart_rate", self._hr, "bpm")
            if self.cadence:
                self.session.ingest_sample(now, 121, 1200, "cadence",
                                           cadence + random.uniform(-2, 2) if cadence else 0, "rpm")
            self.session.ingest_sample(now, 121, 1200, "speed", max(0.0, speed + random.uniform(-0.5, 0.5)), "km/h")
            self._stop.wait(1.0)
