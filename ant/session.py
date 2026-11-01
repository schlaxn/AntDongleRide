"""Owns the USB stick: a reader thread parses frames, decodes sensors and fans
events out to subscribers (the web UI or the CLI)."""
import queue
import struct
import threading
import time
from collections import deque

from .profiles import DEVICE_TYPES, Decoder, Sample
from .protocol import (
    ANTPLUS_NETWORK_KEY,
    ANTPLUS_RF_FREQ,
    EVENT_CODES,
    Frame,
    FrameParser,
    encode,
)
from .usbdev import AntUsb

# Message IDs used for configuration
RESET, SET_NET_KEY, ASSIGN_CH, SET_CH_ID, SET_PERIOD = 0x4A, 0x46, 0x42, 0x51, 0x43
SET_RF_FREQ, OPEN_CH, CLOSE_CH, REQUEST, ENABLE_EXT = 0x45, 0x4B, 0x4C, 0x4D, 0x66
BROADCAST, ACK_DATA, BURST = 0x4E, 0x4F, 0x50
MAX_CHANNELS = 8  # from the stick's capabilities reply (first byte)


class AntSession:
    def __init__(self, wheel_m: float = 2.105):
        self.usb: AntUsb | None = None
        self.parser = FrameParser()
        self.decoder = Decoder(wheel_m)
        self._write_lock = threading.Lock()
        self._sub_lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self.frames: deque = deque(maxlen=5000)  # recent frames for /api/state
        self.samples: deque = deque(maxlen=100_000)  # for CSV export and charts
        self.devices: dict[tuple[int, int], dict] = {}  # (devtype, devnum) -> info
        self.channels: dict[int, int] = {}  # channel -> devtype it listens for
        self.channel_devnum: dict[int, int] = {}  # channel -> pinned device number (0 = wildcard)
        self.events: deque = deque(maxlen=200)  # channel events / errors
        self.startup_flags: int | None = None
        self.bad_checksums = 0
        self._thread: threading.Thread | None = None
        self._running = False

    # ---------- lifecycle ----------
    @property
    def connected(self) -> bool:
        return self.usb is not None

    def connect(self) -> dict:
        if self.connected:
            return self.info()
        self.usb = AntUsb()
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True, name="ant-reader")
        self._thread.start()
        return self.info()

    def disconnect(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self.usb:
            self.usb.close()
        self.usb = None
        self.channels.clear()

    def info(self) -> dict:
        return {"connected": self.connected, "usb": self.usb.info if self.usb else None}

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

    def _publish(self, item: dict) -> None:
        with self._sub_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(item)
            except queue.Full:
                pass  # slow client: drop rather than block the reader

    # ---------- sending ----------
    def send(self, msg_id: int, data: bytes = b"") -> None:
        if not self.usb:
            raise RuntimeError("Not connected")
        raw = encode(msg_id, data)
        frame = Frame(time.time(), "TX", msg_id, bytes(data), raw)
        with self._write_lock:
            self.usb.write(raw)
        self._record(frame)

    def _record(self, frame: Frame) -> None:
        self.frames.append(frame)
        self._publish(frame.to_dict())

    def reset(self) -> None:
        self.send(RESET, b"\x00")

    def request(self, msg_id: int) -> None:
        self.send(REQUEST, bytes([0, msg_id]))

    def configure_wildcard_listen(self, channel: int, devtype: int, period: int | None = None,
                                  net: int = 0, devnum: int = 0) -> None:
        """Set up a slave RX channel for `devtype`. devnum 0 = wildcard (first sensor heard),
        otherwise the channel only accepts that specific sensor."""
        if period is None:
            period = DEVICE_TYPES[devtype][1]
        self.send(SET_NET_KEY, bytes([net]) + ANTPLUS_NETWORK_KEY)
        self.send(ASSIGN_CH, bytes([channel, 0x00, net]))  # 0x00 = receive channel
        self.send(SET_CH_ID, struct.pack("<BHBB", channel, devnum, devtype, 0))
        self.send(SET_PERIOD, struct.pack("<BH", channel, period))
        self.send(SET_RF_FREQ, bytes([channel, ANTPLUS_RF_FREQ]))
        self.send(ENABLE_EXT, bytes([0x00, 0x01]))  # extended data incl. sensor device number
        self.send(OPEN_CH, bytes([channel]))
        self.channels[channel] = devtype
        self.channel_devnum[channel] = devnum
        time.sleep(0.02)

    def _free_channel(self) -> int:
        for ch in range(MAX_CHANNELS):
            if ch not in self.channels:
                return ch
        raise RuntimeError(f"All {MAX_CHANNELS} ANT channels are in use. Close a sensor first.")

    def search(self, devtype: int) -> None:
        """Listen for the first sensor of `devtype` that is in range (wildcard channel)."""
        if any(dt == devtype and self.channel_devnum.get(ch) == 0 for ch, dt in self.channels.items()):
            return
        self.configure_wildcard_listen(self._free_channel(), devtype, devnum=0)

    def pair(self, devtype: int, devnum: int) -> None:
        """Listen only to one specific sensor, on its own channel."""
        if any(dt == devtype and self.channel_devnum.get(ch) == devnum for ch, dt in self.channels.items()):
            return
        self.configure_wildcard_listen(self._free_channel(), devtype, devnum=devnum)

    def close_search(self, devtype: int) -> None:
        """Stop the wildcard search for `devtype` (after a sensor has been paired)."""
        for ch, dt in list(self.channels.items()):
            if dt == devtype and self.channel_devnum.get(ch) == 0:
                self.send(CLOSE_CH, bytes([ch]))
                self.channels.pop(ch, None)
                self.channel_devnum.pop(ch, None)

    def scan(self, devtypes: list[int]) -> None:
        """Listen for several sensor types at once, one wildcard channel each."""
        self.stop_scan()
        for ch, dt in enumerate(devtypes):
            self.configure_wildcard_listen(ch, dt)

    def stop_scan(self) -> None:
        for ch in list(self.channels):
            self.send(CLOSE_CH, bytes([ch]))
        self.channels.clear()
        self.channel_devnum.clear()

    # ---------- receiving ----------
    def _reader(self) -> None:
        while self._running:
            try:
                chunk = self.usb.read(100)
            except Exception as exc:
                self._publish({"type": "error", "message": f"USB read failed: {exc}"})
                self._running = False
                break
            if not chunk:
                continue
            for frame in self.parser.feed(chunk):
                self._handle(frame)
            self.bad_checksums = self.parser.bad_checksums

    def _handle(self, frame: Frame) -> None:
        self._record(frame)
        d = frame.data
        if frame.msg_id == 0x6F and d:
            self.startup_flags = d[0]
        elif frame.msg_id == 0x40 and len(d) >= 3 and d[1] == 0x01 and d[2] == 0x07:
            # CHANNEL_CLOSED: the stick has released this channel. Forget it, otherwise
            # search() and pair() think it is still in use and new searches never start.
            self.channels.pop(d[0], None)
            self.channel_devnum.pop(d[0], None)
            self._log_event(frame, f"ch{d[0]} CHANNEL_CLOSED")
        elif frame.msg_id == 0x40 and len(d) >= 3 and d[1] == 0x01:
            self._log_event(frame, f"ch{d[0]} {EVENT_CODES.get(d[2], hex(d[2]))}")
        elif frame.msg_id == 0x40 and len(d) >= 3 and d[2] != 0:
            self._log_event(frame, f"ch{d[0]} error on 0x{d[1]:02X}: 0x{d[2]:02X}")
        elif frame.msg_id in (BROADCAST, ACK_DATA, BURST) and len(d) >= 9:
            self._handle_data(frame)

    def _log_event(self, frame: Frame, text: str) -> None:
        item = {"ts": frame.ts, "text": text}
        self.events.append(item)
        self._publish({"type": "event", **item})

    def _handle_data(self, frame: Frame) -> None:
        d = frame.data
        chan, payload = d[0], d[1:9]
        devtype = self.channels.get(chan, 0)
        devnum = 0
        if len(d) >= 14 and d[9] & 0x80:  # extended data: channel ID present
            devnum = struct.unpack_from("<H", d, 10)[0]
            devtype = d[12]
        key = (devtype, devnum)
        info = self.devices.setdefault(key, {"devtype": devtype, "devnum": devnum,
                                             "count": 0, "first": frame.ts})
        info["count"] += 1
        info["last"] = frame.ts
        info["channel"] = chan
        for s in self.decoder.decode(devtype, devnum, payload, frame.ts):
            self.samples.append(s)
            self._publish(s.to_dict())

    # ---------- non-ANT sources (Bluetooth heart rate) ----------
    def ingest_ble_bpm(self, ts: float, bpm: int, raw: bytes) -> None:
        """Heart rate from the laptop's Bluetooth adapter, shown alongside ANT data (devnum -1)."""
        key = (120, -1)
        info = self.devices.setdefault(key, {"devtype": 120, "devnum": -1, "count": 0,
                                             "first": ts, "channel": "BLE"})
        info["count"] += 1
        info["last"] = ts
        frame = Frame(ts, "BLE", 0x2A37, raw, raw)
        self.frames.append(frame)
        self._publish({**frame.to_dict(), "name": "HR_MEASUREMENT", "desc": f"bpm {bpm}"})
        s = Sample(ts, 120, -1, "heart_rate", bpm, "bpm")
        self.samples.append(s)
        self._publish(s.to_dict())

    def ingest_sample(self, ts: float, devtype: int, devnum: int, metric: str,
                      value: float, unit: str) -> None:
        """Feed an already-decoded sample into the stream (used by the simulator)."""
        info = self.devices.setdefault((devtype, devnum), {"devtype": devtype, "devnum": devnum,
                                                           "count": 0, "first": ts, "channel": "sim"})
        info["count"] += 1
        info["last"] = ts
        s = Sample(ts, devtype, devnum, metric, round(value, 2), unit)
        self.samples.append(s)
        self._publish(s.to_dict())

    def publish(self, item: dict) -> None:
        """Public pub/sub entry point (used by the ride engine for its ticks)."""
        self._publish(item)

    def ble_event(self, text: str) -> None:
        item = {"ts": time.time(), "text": text}
        self.events.append(item)
        self._publish({"type": "event", **item})

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "usb": self.usb.info if self.usb else None,
            "channels": {str(k): v for k, v in self.channels.items()},
            "startup_flags": self.startup_flags,
            "bad_checksums": self.bad_checksums,
            "devices": [
                {
                    **v,
                    "name": DEVICE_TYPES.get(v["devtype"], (f"type {v['devtype']}",))[0],
                }
                for v in self.devices.values()
            ],
            "events": list(self.events)[-30:],
        }
