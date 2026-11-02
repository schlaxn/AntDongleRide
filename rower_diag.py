"""Staged check of the WaterRower S4 USB link, like diag.py does for the ANT stick.

    python rower_diag.py                      # pick the port automatically
    python rower_diag.py --port COM5          # use a specific port
    python rower_diag.py --port COM5 --baud 9600 --listen 20

Stages: ENUMERATE -> OPEN (baud sweep if no baud given) -> HANDSHAKE -> LISTEN.
LISTEN prints every received line with a timestamp; pull the handle to see strokes.
"""
import argparse
import sys
import time

from rower.serialdev import BAUD_CANDIDATES, RowerError, RowerSerial, list_ports

HANDSHAKE = b"USB\r\n"  # guess from community code; Phase 0 is meant to confirm or refute it


def _report(stage: str, status: str, detail: str = "") -> None:
    print(f"[{status:4}] {stage:<10} {detail}")


def _stage_enumerate(port_arg: str | None) -> str | None:
    try:
        ports = list_ports()
    except ImportError:
        _report("ENUMERATE", "FAIL", "pyserial is not installed (pip install -r requirements.txt)")
        return None
    if not ports:
        _report("ENUMERATE", "FAIL", "no serial ports found; check the cable and Device Manager")
        return None
    for p in ports:
        ids = f" vid={p['vid']} pid={p['pid']}" if p["vid"] else ""
        print(f"         {p['device']}: {p['description']}{ids} mfr={p['manufacturer']!r}")
    if port_arg:
        _report("ENUMERATE", "PASS" if any(p["device"] == port_arg for p in ports) else "WARN",
                f"using {port_arg} (not in the list above)" if not any(p["device"] == port_arg for p in ports) else f"using {port_arg}")
        return port_arg
    usb_like = [p for p in ports if any(k in (p["description"] + p["hwid"]).upper()
                                        for k in ("USB", "FTDI", "CH340", "CP210", "PL2303", "SERIAL"))]
    pick = usb_like or ports
    if len(pick) == 1:
        _report("ENUMERATE", "PASS", f"picked {pick[0]['device']}")
        return pick[0]["device"]
    _report("ENUMERATE", "WARN", "several candidate ports; rerun with --port")
    return None


def _open_and_handshake(rower: RowerSerial, port: str, baud: int, wait: float) -> int:
    rower.connect(port, baud)
    rower_bytes_before = rower.rx_bytes
    rower.send(HANDSHAKE)
    time.sleep(wait)
    got = rower.rx_bytes - rower_bytes_before
    rower.disconnect()
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="WaterRower S4 USB link check")
    ap.add_argument("--port", help="serial port, e.g. COM5 (default: auto)")
    ap.add_argument("--baud", type=int, help="baud rate (default: sweep the common rates)")
    ap.add_argument("--listen", type=float, default=10.0, help="seconds to listen (default 10)")
    ap.add_argument("--wait", type=float, default=1.5, help="seconds to wait for a handshake reply")
    args = ap.parse_args(argv)

    port = _stage_enumerate(args.port)
    if not port:
        return 1

    rower = RowerSerial()
    bauds = [args.baud] if args.baud else BAUD_CANDIDATES
    chosen = None
    for baud in bauds:
        try:
            got = _open_and_handshake(rower, port, baud, args.wait)
        except RowerError as exc:
            _report("OPEN", "FAIL", str(exc))
            return 1
        except Exception as exc:
            _report("OPEN", "FAIL", f"{port} @ {baud}: {exc}")
            return 1
        if got:
            chosen = baud
            _report("OPEN", "PASS", f"{port} @ {baud} baud")
            _report("HANDSHAKE", "PASS", f"{got} bytes came back after {HANDSHAKE!r}")
            break
        print(f"         {baud} baud: no reply to the handshake, trying next rate")
    if chosen is None:
        if args.baud:
            _report("OPEN", "WARN", f"{port} opened at {args.baud} baud but stayed silent")
        else:
            _report("OPEN", "WARN", "port opens but no reply at any common baud rate")
        _report("HANDSHAKE", "WARN", "no reply; the monitor may need a different command or baud rate")
        chosen = args.baud or BAUD_CANDIDATES[0]

    rower.connect(port, chosen)
    print(f"[ .. ] LISTEN     {args.listen:.0f} s at {chosen} baud; pull the handle to see activity")
    rx_start = rower.rx_bytes
    end = time.time() + args.listen
    seen = 0
    q = rower.subscribe()
    try:
        while time.time() < end:
            try:
                item = q.get(timeout=0.2)
            except Exception:
                continue
            if item.get("dir") == "RX":
                seen += 1
                stamp = time.strftime("%H:%M:%S", time.localtime(item["ts"])) + f".{int((item['ts'] % 1) * 1000):03d}"
                print(f"         {stamp} {item['raw_hex']}  {item['text']}")
                for line in item.get("lines", []):
                    print(f"         {stamp}   line: {line}")
    finally:
        rower.unsubscribe(q)
        rower.disconnect()
    total = rower.rx_bytes - rx_start
    _report("LISTEN", "PASS" if total else "WARN", f"{total} bytes in {seen} chunks")
    return 0 if chosen else 1


if __name__ == "__main__":
    sys.exit(main())
