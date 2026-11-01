"""Bluetooth LE heart-rate source (standard Heart Rate Service 0x180D / 0x2A37).

Uses the laptop's own Bluetooth adapter via bleak. Runs its own asyncio loop in a
background thread and reports decoded heart-rate samples through callbacks.
"""
import asyncio
import threading
import time

from bleak import BleakClient, BleakScanner

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"
CSC_SERVICE = "00001816-0000-1000-8000-00805f9b34fb"       # Cycling Speed and Cadence
CSC_MEASUREMENT = "00002a5b-0000-1000-8000-00805f9b34fb"


def parse_csc_measurement(data: bytes) -> dict:
    """CSC Measurement: a flags byte, then optionally the cumulative wheel revolutions (u32) with the
    last wheel event time (u16, 1/1024 s), and the cumulative crank revolutions (u16) with the last
    crank event time (u16, 1/1024 s). Returns only the fields that are present."""
    out = {}
    if not data:
        return out
    flags, i = data[0], 1
    if flags & 0x01 and len(data) >= i + 6:
        out["wheel_revs"] = int.from_bytes(data[i:i + 4], "little")
        out["wheel_t"] = int.from_bytes(data[i + 4:i + 6], "little")
        i += 6
    if flags & 0x02 and len(data) >= i + 4:
        out["crank_revs"] = int.from_bytes(data[i:i + 2], "little")
        out["crank_t"] = int.from_bytes(data[i + 2:i + 4], "little")
    return out


def csc_rates(prev: dict, cur: dict, wheel_m: float) -> dict:
    """Speed (km/h) and cadence (rpm) from two consecutive CSC measurements. A field is only
    returned when both measurements carry it and the event time moved on."""
    res = {}
    if "wheel_revs" in cur and "wheel_revs" in prev:
        dt = ((cur["wheel_t"] - prev["wheel_t"]) % 65536) / 1024.0
        dr = (cur["wheel_revs"] - prev["wheel_revs"]) % (2 ** 32)
        if 0 < dt <= 10:
            res["speed_kmh"] = dr * wheel_m / dt * 3.6
    if "crank_revs" in cur and "crank_revs" in prev:
        dt = ((cur["crank_t"] - prev["crank_t"]) % 65536) / 1024.0
        dr = (cur["crank_revs"] - prev["crank_revs"]) % 65536
        if 0 < dt <= 10:
            res["cadence_rpm"] = dr * 60.0 / dt
    return res


def parse_hr_measurement(data: bytes) -> int | None:
    """Heart Rate Measurement characteristic: flags byte, then 8- or 16-bit bpm."""
    if not data:
        return None
    flags = data[0]
    if flags & 0x01:  # 16-bit heart rate value
        if len(data) < 3:
            return None
        bpm = int.from_bytes(data[1:3], "little")
    else:
        if len(data) < 2:
            return None
        bpm = data[1]
    return bpm if bpm > 0 else None


async def scan(seconds: float = 6.0) -> list[dict]:
    """Return nearby BLE devices; devices advertising the HR service are flagged."""
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    out = []
    for device, adv in found.values():
        services = [s.lower() for s in (adv.service_uuids or [])]
        out.append({
            "address": device.address,
            "name": device.name or adv.local_name or "(no name)",
            "rssi": adv.rssi,
            "heart_rate": HR_SERVICE in services,
        })
    for d, (device, adv) in zip(out, found.values()):
        services = [s.lower() for s in (adv.service_uuids or [])]
        d["csc"] = CSC_SERVICE in services
    out.sort(key=lambda d: (not (d["heart_rate"] or d["csc"]), -(d["rssi"] or -999)))
    return out


class BleHeartRate:
    """Keeps a connection to one heart-rate device and reconnects if it drops."""

    def __init__(self, on_bpm, on_event):
        self._on_bpm = on_bpm  # callback(ts, bpm, raw_bytes)
        self._on_event = on_event  # callback(text)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.address: str | None = None
        self.state = "idle"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, address: str) -> None:
        self.stop()
        self.address = address
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(address,), daemon=True, name="ble-hr")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self.state = "idle"

    def _run(self, address: str) -> None:
        asyncio.run(self._main(address))

    async def _main(self, address: str) -> None:
        while not self._stop.is_set():
            try:
                self.state = "connecting"
                self._on_event(f"BLE connecting to {address}")
                async with BleakClient(address, timeout=15) as client:
                    self.state = "connected"
                    self._on_event(f"BLE connected to {address}")

                    def handler(_char, data: bytearray):
                        bpm = parse_hr_measurement(bytes(data))
                        if bpm is not None:
                            self._on_bpm(time.time(), bpm, bytes(data))

                    await client.start_notify(HR_MEASUREMENT, handler)
                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)
                    await client.stop_notify(HR_MEASUREMENT)
                if not self._stop.is_set():
                    self._on_event("BLE disconnected, reconnecting")
            except Exception as exc:
                self.state = "error"
                self._on_event(f"BLE error: {exc}")
            if not self._stop.is_set():
                await asyncio.sleep(3)
        self.state = "idle"


class BleCsc:
    """Keeps a connection to one Bluetooth speed and/or cadence sensor (Cycling Speed and Cadence
    service). Turns the cumulative counts into speed (km/h) and cadence (rpm) samples."""

    def __init__(self, on_sample, on_event, wheel_m):
        self._on_sample = on_sample   # callback(ts, metric, value, unit)
        self._on_event = on_event     # callback(text)
        self._wheel_m = wheel_m       # callable -> wheel circumference (m)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._prev: dict = {}
        self._notified = False    # any measurement received on this connection
        self._crank_seen = False  # crank (cadence) data received on this connection
        self.address: str | None = None
        self.state = "idle"

    @property
    def has_crank(self) -> bool | None:
        """True if the sensor reports cadence, False if it is speed only, None until the first measurement."""
        if not self._notified:
            return None
        return self._crank_seen

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, address: str) -> None:
        self.stop()
        self.address = address
        self._prev = {}
        self._notified = False
        self._crank_seen = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(address,), daemon=True, name="ble-csc")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None
        self.state = "idle"

    def _run(self, address: str) -> None:
        asyncio.run(self._main(address))

    def _handle(self, data: bytes) -> None:
        cur = parse_csc_measurement(data)
        if not cur:
            return
        self._notified = True
        if "crank_revs" in cur:
            self._crank_seen = True
        ts = time.time()
        rates = csc_rates(self._prev, cur, self._wheel_m())
        self._prev.update(cur)
        if "speed_kmh" in rates:
            self._on_sample(ts, "speed", round(rates["speed_kmh"], 2), "km/h")
        if "cadence_rpm" in rates:
            self._on_sample(ts, "cadence", round(rates["cadence_rpm"], 1), "rpm")

    async def _main(self, address: str) -> None:
        while not self._stop.is_set():
            try:
                self.state = "connecting"
                self._on_event(f"BLE speed sensor connecting to {address}")
                async with BleakClient(address, timeout=15) as client:
                    self.state = "connected"
                    self._on_event(f"BLE speed sensor connected to {address}")
                    self._prev = {}

                    def handler(_char, data: bytearray):
                        self._handle(bytes(data))

                    await client.start_notify(CSC_MEASUREMENT, handler)
                    while client.is_connected and not self._stop.is_set():
                        await asyncio.sleep(0.5)
                    await client.stop_notify(CSC_MEASUREMENT)
                if not self._stop.is_set():
                    self._on_event("BLE speed sensor disconnected, reconnecting")
            except Exception as exc:
                self.state = "error"
                self._on_event(f"BLE speed sensor error: {exc}")
            if not self._stop.is_set():
                await asyncio.sleep(3)
        self.state = "idle"
