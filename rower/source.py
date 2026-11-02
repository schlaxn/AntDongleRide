"""Adapter: the S4 serial stream -> decoded samples -> the rowing hub.

RowerSerial (serialdev.py) publishes raw chunks with the complete ASCII lines already
split out. This thread takes each RX line, runs it through the stateful Decoder, and
ingests the resulting samples into the hub.
"""
import queue
import threading

from .profiles import Decoder
from .serialdev import DEFAULT_BAUD, RowerError, RowerSerial


class RowerSource:
    def __init__(self, serial: RowerSerial, hub, decoder: Decoder | None = None):
        self.serial = serial
        self.hub = hub
        self.decoder = decoder or Decoder()
        self._q: queue.Queue | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self.serial.connected

    def info(self) -> dict:
        return {**self.serial.info(), "decoder": {"fill_litres": self.decoder.fill_litres}}

    def connect(self, port: str, baud: int = DEFAULT_BAUD) -> dict:
        info = self.serial.connect(port, baud)
        self._q = self.serial.subscribe()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rower-source")
        self._thread.start()
        return info

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        if self._q is not None:
            self.serial.unsubscribe(self._q)
            self._q = None
        self.serial.disconnect()

    def _loop(self) -> None:
        q = self._q
        while not self._stop.is_set():
            try:
                item = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item.get("dir") != "RX":
                continue
            for line in item.get("lines", []):
                for s in self.decoder.decode_line(line, item["ts"]):
                    self.hub.ingest_sample(s)


__all__ = ["RowerSource", "RowerError"]
