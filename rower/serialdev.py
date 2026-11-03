"""Serial link to the WaterRower S4 monitor. Owns the port, reads it on a thread and
fans every received/sent chunk out to subscribers (the debug page or the CLI).

Phase 0 only moves bytes: nothing here interprets the monitor's protocol yet.
"""
import codecs
import queue
import threading
import time
from collections import deque

DEFAULT_BAUD = 115200
BAUD_CANDIDATES = [115200, 9600, 19200, 38400, 57600]


class RowerError(Exception):
    pass


def printable(data: bytes) -> str:
    """ASCII view of raw bytes; control characters become escapes like \\r or \\x1b."""
    out = []
    for c in data:
        if c == 0x0D:
            out.append("\\r")
        elif c == 0x0A:
            out.append("\\n")
        elif 0x20 <= c < 0x7F:
            out.append(chr(c))
        else:
            out.append(f"\\x{c:02x}")
    return "".join(out)


def split_lines(buf: bytearray) -> list[str]:
    """Remove complete lines (terminated by \\n; a preceding \\r is dropped) from buf in place.
    Bytes after the last \\n stay in buf until more data arrives."""
    lines = []
    while True:
        idx = buf.find(b"\n")
        if idx < 0:
            return lines
        lines.append(bytes(buf[:idx]).rstrip(b"\r").decode("ascii", "replace"))
        del buf[:idx + 1]


def parse_payload(text: str, mode: str) -> bytes:
    """Turn the debug page's input into bytes. mode 'ascii' understands escapes such as
    \\r\\n; mode 'hex' takes hex pairs, e.g. '55 53 42 0d 0a'."""
    if mode == "hex":
        try:
            return bytes.fromhex(text.replace(" ", ""))
        except ValueError as exc:
            raise ValueError(f"Not valid hex: {exc}") from exc
    if mode == "ascii":
        return codecs.decode(text, "unicode_escape").encode("latin-1", "replace")
    raise ValueError(f"Unknown mode {mode!r}")


def open_port(port: str, baud: int):
    import serial  # pyserial; imported lazily so the module loads without it

    return serial.Serial(port=port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                         timeout=0.2, write_timeout=1)


def list_ports() -> list[dict]:
    from serial.tools import list_ports as lp

    rows = []
    for p in lp.comports():
        rows.append({
            "device": p.device,
            "description": p.description or "",
            "manufacturer": p.manufacturer or "",
            "product": p.product or "",
            "serial_number": p.serial_number or "",
            "vid": f"{p.vid:04X}" if p.vid is not None else "",
            "pid": f"{p.pid:04X}" if p.pid is not None else "",
            "hwid": p.hwid or "",
        })
    return rows


class RowerSerial:
    def __init__(self, opener=None):
        self._opener = opener or open_port
        self._ser = None
        self.port: str | None = None
        self.baud: int | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._buf = bytearray()
        self._write_lock = threading.Lock()
        self._sub_lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self.events: deque = deque(maxlen=50_000)  # every RX/TX/EVT item, for export
        self.rx_bytes = 0
        self.tx_bytes = 0

    # ---------- lifecycle ----------
    @property
    def connected(self) -> bool:
        return self._ser is not None

    def info(self) -> dict:
        return {"connected": self.connected, "port": self.port, "baud": self.baud,
                "rx_bytes": self.rx_bytes, "tx_bytes": self.tx_bytes}

    def connect(self, port: str, baud: int = DEFAULT_BAUD) -> dict:
        if self.connected:
            raise RowerError(f"Already connected to {self.port}. Disconnect first.")
        self._ser = self._opener(port, baud)
        self.port, self.baud = port, baud
        self._buf.clear()
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True, name="rower-reader")
        self._thread.start()
        self._event(f"connected to {port} at {baud} baud")
        # the S4 streams only after the handshake (see ROWER_HANDOVER.md, section 2)
        self.send(b"USB\r\n")
        return self.info()

    def disconnect(self) -> None:
        if not self.connected:
            return
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        try:
            self._ser.close()
        except Exception:
            pass
        self._ser = None
        self._event(f"disconnected from {self.port}")
        self.port = None

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

    def _emit(self, item: dict) -> None:
        self.events.append(item)
        self._publish(item)

    def _event(self, text: str) -> None:
        self._emit({"type": "rower", "ts": time.time(), "dir": "EVT", "text": text,
                    "raw_hex": "", "len": 0})

    # ---------- traffic ----------
    def send(self, data: bytes) -> None:
        if not self.connected:
            raise RowerError("Not connected")
        with self._write_lock:
            self._ser.write(data)
        self.tx_bytes += len(data)
        self._emit({"type": "rower", "ts": time.time(), "dir": "TX", "len": len(data),
                    "raw_hex": data.hex(" "), "text": printable(data), "lines": []})

    def _reader(self) -> None:
        while self._running:
            try:
                chunk = self._ser.read(256)
            except Exception as exc:
                self._event(f"read failed (monitor off or unplugged?): {exc}")
                self._running = False
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None          # not connected any more: the watchdog reopens the port
                break
            if not chunk:
                continue
            self.rx_bytes += len(chunk)
            self._buf.extend(chunk)
            lines = split_lines(self._buf)
            self._emit({"type": "rower", "ts": time.time(), "dir": "RX", "len": len(chunk),
                        "raw_hex": chunk.hex(" "), "text": printable(chunk), "lines": lines})
