"""Shared sample hub for the rowing app: the rower twin of ant/session.py's pub/sub.

Sources (the S4 decoder, Bluetooth heart rate, the simulator) push samples in. The
engine and the browser stream subscribe. Nothing here knows about physics.
"""
import queue
import threading
import time
from collections import deque

from .profiles import ROWER_DEVNUM, ROWER_DEVTYPE, Sample

BLE_HR_DEVTYPE = 120
BLE_HR_DEVNUM = -1


class RowHub:
    def __init__(self, samples_max: int = 100_000):
        self._sub_lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self.samples: deque = deque(maxlen=samples_max)
        self.devices: dict[tuple[int, int], dict] = {}   # (devtype, devnum) -> info
        self.events: deque = deque(maxlen=200)

    # ---------- pub/sub ----------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self._sub_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, item: dict) -> None:
        with self._sub_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(item)
            except queue.Full:
                pass  # slow client: drop rather than block the producer

    # ---------- ingest ----------
    def ingest_sample(self, s: Sample) -> None:
        key = (s.devtype, s.devnum)
        info = self.devices.setdefault(key, {"devtype": s.devtype, "devnum": s.devnum,
                                             "count": 0, "first": s.ts})
        info["count"] += 1
        info["last"] = s.ts
        self.samples.append(s)
        self.publish(s.to_dict())

    def ingest_ble_bpm(self, ts: float, bpm: int) -> None:
        """Heart rate from a Bluetooth watch, using the ride app's convention (devtype 120, devnum -1)."""
        self.ingest_sample(Sample(ts, "heart_rate", int(bpm), "bpm",
                                  devtype=BLE_HR_DEVTYPE, devnum=BLE_HR_DEVNUM))

    def event(self, text: str) -> None:
        item = {"ts": time.time(), "text": text}
        self.events.append(item)
        self.publish({"type": "event", **item})

    def status(self) -> dict:
        now = time.time()
        return {
            "devices": [
                {**v, "age_s": round(now - v.get("last", now), 1)}
                for v in self.devices.values()
            ],
            "events": list(self.events)[-30:],
        }


__all__ = ["RowHub", "ROWER_DEVTYPE", "ROWER_DEVNUM", "BLE_HR_DEVTYPE", "BLE_HR_DEVNUM"]
