"""ANT serial framing, message IDs and human-readable descriptions.

Frame layout:  SYNC(0xA4) | LEN | MSG_ID | DATA[LEN] | CHECKSUM
CHECKSUM is the XOR of every preceding byte of the frame.
"""
import struct
import time
from dataclasses import dataclass, field

SYNC = 0xA4

ANTPLUS_NETWORK_KEY = bytes([0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45])
ANTPLUS_RF_FREQ = 57  # 2457 MHz, the ANT+ channel frequency offset

# Message IDs (subset relevant to a receive-only diagnostic tool)
MSG_NAMES = {
    0x3E: "ANT_VERSION",
    0x40: "CHANNEL_EVENT",
    0x42: "ASSIGN_CHANNEL",
    0x43: "SET_CHANNEL_PERIOD",
    0x45: "SET_RF_FREQ",
    0x46: "SET_NETWORK_KEY",
    0x4A: "RESET_SYSTEM",
    0x4B: "OPEN_CHANNEL",
    0x4C: "CLOSE_CHANNEL",
    0x4D: "REQUEST_MESSAGE",
    0x4E: "BROADCAST_DATA",
    0x4F: "ACKNOWLEDGED_DATA",
    0x50: "BURST_DATA",
    0x51: "CHANNEL_ID",
    0x54: "CAPABILITIES",
    0x61: "SERIAL_NUMBER",
    0x66: "ENABLE_EXT_RX_MESSAGES",
    0x6F: "STARTUP",
}

# Channel event / response codes (msg 0x40, message id 0x01 = event)
EVENT_CODES = {
    0x01: "RX_SEARCH_TIMEOUT",
    0x02: "RX_FAIL",
    0x03: "TX",
    0x04: "TRANSFER_RX_FAILED",
    0x05: "TRANSFER_TX_COMPLETED",
    0x06: "TRANSFER_TX_FAILED",
    0x07: "CHANNEL_CLOSED",
    0x08: "RX_FAIL_GO_TO_SEARCH",
    0x09: "CHANNEL_COLLISION",
    0x0A: "TRANSFER_TX_START",
}
RESPONSE_ERRORS = {
    0x00: "NO_ERROR",
    0x15: "CHANNEL_IN_WRONG_STATE",
    0x16: "CHANNEL_NOT_OPENED",
    0x18: "CHANNEL_ID_NOT_SET",
    0x19: "CLOSE_ALL_CHANNELS",
    0x28: "INVALID_MESSAGE",
    0x29: "INVALID_NETWORK",
}


@dataclass
class Frame:
    ts: float
    direction: str  # "RX" (from stick) or "TX" (to stick)
    msg_id: int
    data: bytes
    raw: bytes = field(repr=False, default=b"")

    @property
    def name(self) -> str:
        return MSG_NAMES.get(self.msg_id, f"UNKNOWN_0x{self.msg_id:02X}")

    def to_dict(self) -> dict:
        return {
            "type": "frame",
            "ts": self.ts,
            "dir": self.direction,
            "id": self.msg_id,
            "name": self.name,
            "hex": self.raw.hex(" ").upper(),
            "desc": describe(self.msg_id, self.data),
        }


def checksum(body: bytes) -> int:
    c = 0
    for b in body:
        c ^= b
    return c


def encode(msg_id: int, data: bytes = b"") -> bytes:
    body = bytes([SYNC, len(data), msg_id]) + bytes(data)
    return body + bytes([checksum(body)])


class FrameParser:
    """Incremental parser: feed arbitrary byte chunks, get complete frames back."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_checksums = 0

    def feed(self, chunk: bytes, ts: float | None = None) -> list[Frame]:
        ts = time.time() if ts is None else ts
        self.buf.extend(chunk)
        out: list[Frame] = []
        while True:
            i = self.buf.find(SYNC)
            if i < 0:
                self.buf.clear()
                break
            if i:
                del self.buf[:i]
            if len(self.buf) < 2:
                break
            total = self.buf[1] + 4
            if len(self.buf) < total:
                break
            raw = bytes(self.buf[:total])
            if checksum(raw[:-1]) != raw[-1]:
                self.bad_checksums += 1
                del self.buf[0]  # resync: drop the sync byte and look again
                continue
            del self.buf[:total]
            out.append(Frame(ts, "RX", raw[2], raw[3:-1], raw))
        return out


def describe(msg_id: int, data: bytes) -> str:
    """Short human-readable summary of a message payload."""
    try:
        if msg_id == 0x40 and len(data) >= 3:
            chan, mid, code = data[0], data[1], data[2]
            if mid == 0x01:
                return f"ch{chan} EVENT {EVENT_CODES.get(code, hex(code))}"
            return f"ch{chan} response to 0x{mid:02X}: {RESPONSE_ERRORS.get(code, hex(code))}"
        if msg_id in (0x4E, 0x4F, 0x50) and data:
            s = f"ch{data[0]} payload {data[1:9].hex(' ').upper()}"
            if len(data) >= 14 and data[9] & 0x80:
                devnum, devtype = struct.unpack_from("<H", data, 10)[0], data[12]
                s += f" | sensor devnum={devnum} devtype={devtype}"
            return s
        if msg_id == 0x6F and data:
            return f"startup flags 0x{data[0]:02X}"
        if msg_id == 0x3E:
            return "ANT firmware: " + data.decode("ascii", "replace").strip("\x00")
        if msg_id == 0x61 and len(data) >= 4:
            return f"serial {struct.unpack_from('<I', data, 0)[0]}"
        if msg_id == 0x54 and data:
            return "capabilities " + data.hex(" ").upper()
        if msg_id == 0x51 and len(data) >= 5:
            return f"ch{data[0]} channel id devnum={struct.unpack_from('<H', data, 1)[0]} devtype={data[3]}"
    except Exception as exc:  # never let a display helper break the stream
        return f"(decode error: {exc})"
    return ""
