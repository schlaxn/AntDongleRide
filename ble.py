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
    out.sort(key=lambda d: (not d["heart_rate"], -(d["rssi"] or -999)))
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
