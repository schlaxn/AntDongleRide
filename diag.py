"""Stage-by-stage self test for the ANT USB stick and nearby ANT+ sensors.

    python diag.py                 # scan all common sensor types for 30 s
    python diag.py --type 120      # only heart rate, 60 s
    python diag.py --seconds 15

Stages:
  1. USB      - find and open the stick (driver / access problems show up here)
  2. Reset    - reset the chip, expect a STARTUP message
  3. Firmware - ask for ANT version / capabilities / serial (proves the chip talks)
  4. Listen   - open wildcard RX channels and wait for sensor broadcasts
"""
import argparse
import sys
import time

from ant.profiles import DEVICE_TYPES
from ant.session import AntSession
from ant.usbdev import AntUsbError

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


def stage(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")


def result(kind: str, msg: str) -> None:
    print(f"    {kind}: {msg}")


def wait_for(session: AntSession, pred, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--type", type=int, action="append", help="device type(s) to listen for (default: all known)")
    ap.add_argument("--seconds", type=float, default=30, help="listen time (default 30)")
    args = ap.parse_args()
    devtypes = args.type or list(DEVICE_TYPES)

    session = AntSession()
    verdict = {}

    # 1. USB
    stage(1, "USB: find and open the stick")
    try:
        session.connect()
        result(PASS, f"opened {session.info()['usb']}")
        verdict["usb"] = True
    except AntUsbError as exc:
        result(FAIL, str(exc))
        return _verdict(verdict, sys.stdout)
    except Exception as exc:
        result(FAIL, f"unexpected USB error: {exc}. If this mentions 'No backend' or 'libusb', "
                     "the Dynastream driver (libusb0.dll) is missing.")
        return _verdict(verdict, sys.stdout)

    try:
        # 2. Reset
        stage(2, "Reset chip, expect STARTUP message")
        session.reset()
        ok = wait_for(session, lambda: session.startup_flags is not None, 3)
        if ok:
            result(PASS, f"startup message received (flags 0x{session.startup_flags:02X})")
        else:
            result(WARN, "no startup message within 3 s - continuing anyway")
        verdict["reset"] = ok
        time.sleep(0.3)

        # 3. Firmware
        stage(3, "Firmware query: ANT version, capabilities, serial")
        got = {}
        for mid, label in ((0x3E, "version"), (0x54, "capabilities"), (0x61, "serial")):
            session.request(mid)
            wait_for(session, lambda: any(f.msg_id == mid and f.direction == "RX"
                                          for f in list(session.frames)), 2)
            reply = [f for f in list(session.frames) if f.msg_id == mid and f.direction == "RX"]
            if reply:
                got[label] = reply[-1]
                result(PASS, f"{label}: {reply[-1].to_dict()['desc']}")
            else:
                result(FAIL, f"no {label} reply")
        verdict["firmware"] = "version" in got
        if not verdict["firmware"]:
            result("HINT", "The stick does not answer protocol messages. Try another USB port, "
                           "a USB 2.0 port, and check Device Manager for yellow warnings.")
            return _verdict(verdict, sys.stdout)

        # 4. Listen
        stage(4, f"Listen for sensors on types {devtypes} for {args.seconds:.0f} s")
        session.scan(devtypes)
        for ch, dt in session.channels.items():
            result("..", f"channel {ch} -> {DEVICE_TYPES.get(dt, ('?',))[0]} (devtype {dt})")
        start = time.time()
        last = 0
        while time.time() - start < args.seconds:
            time.sleep(1)
            n = len([d for d in session.devices.values()])
            if n != last:
                last = n
                print(f"    ... {time.time() - start:4.0f} s: {n} sensor(s) heard so far")
        time.sleep(0.3)

        errors = [e for e in session.events if "error" in e["text"] or "SEARCH_TIMEOUT" in e["text"]]
        for e in errors[-5:]:
            result(WARN, e["text"])

        if session.devices:
            verdict["sensors"] = True
            for (dt, dn), info in session.devices.items():
                name = DEVICE_TYPES.get(dt, (f"type {dt}",))[0]
                rate = info["count"] / max(info["last"] - info["first"], 0.001)
                result(PASS, f"{name} devnum={dn} devtype={dt}: {info['count']} msgs (~{rate:.1f}/s)")
        else:
            verdict["sensors"] = False
            result(FAIL, "no sensor broadcasts received on any listened channel")
        return _verdict(verdict, sys.stdout, session)
    finally:
        try:
            session.stop_scan()
        except Exception:
            pass
        session.disconnect()


def _verdict(v: dict, out, session: AntSession | None = None) -> int:
    print("\nVerdict")
    if not v.get("usb"):
        print("  - Stick not reachable over USB: check cable/port, Device Manager, and driver (Dynastream / libusb-win32).")
        return 2
    if not v.get("firmware"):
        print("  - Stick is reachable but does not answer ANT messages: stick or USB-level fault suspected.")
        return 3
    if v.get("sensors"):
        print("  - Dongle and driver work, and at least one sensor is transmitting. Use app.py to inspect it.")
        return 0
    print("  - Dongle and driver work, but no sensor was heard. Check, in order:")
    print("      * sensor battery (Garmin HR straps often need a fresh CR2032, moisten the electrodes)")
    print("      * wake the sensor (spin the crank, wear the strap, move the footpod)")
    print("      * sensor is within ~1 m and no other app (Garmin ANT Agent, Zwift, watch) holds the channel")
    print("      * the sensor is an ANT+ model (some newer sensors are BLE-only)")
    print("      * try the wildcard scan again with --seconds 60 and all types")
    return 4


if __name__ == "__main__":
    sys.exit(main())
