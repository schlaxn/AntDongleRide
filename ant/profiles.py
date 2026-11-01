"""ANT+ device profile decoders (heart rate, bike power, bike speed / cadence).

Only the pages needed for the common Garmin sensors are decoded. Everything else
is still visible as raw packets in the debug UI.

Layouts follow the public ANT+ device profile pages as I know them; if a value
looks wrong on real hardware, compare the raw hex in the packet log first.
"""
from dataclasses import dataclass

# device type -> (name, channel period in 32768 Hz ticks)
DEVICE_TYPES = {
    120: ("Heart rate", 8070),
    11: ("Bike power", 8182),
    121: ("Bike speed & cadence", 8086),
    122: ("Bike cadence", 8102),
    123: ("Bike speed", 8118),
}

DEFAULT_WHEEL_M = 2.105  # typical 700x23c wheel circumference


@dataclass
class Sample:
    ts: float
    devtype: int
    devnum: int
    metric: str
    value: float
    unit: str

    def to_dict(self) -> dict:
        return {"type": "sample", **self.__dict__}


def _u16(b: bytes, i: int) -> int:
    return b[i] | (b[i + 1] << 8)


class _Delta:
    """Turns cumulative 16-bit event time (1/1024 s) and revolution counters into deltas."""

    def __init__(self):
        self.prev = None

    def step(self, t: int, r: int):
        if self.prev is None:
            self.prev = (t, r)
            return None
        pt, pr = self.prev
        dt = (t - pt) & 0xFFFF
        dr = (r - pr) & 0xFFFF
        if dt == 0:  # sensor repeated the same event: nothing new
            return None
        self.prev = (t, r)
        seconds = dt / 1024.0
        if seconds > 10:  # gap too large to be meaningful
            return None
        return seconds, dr


class Decoder:
    def __init__(self, wheel_m: float = DEFAULT_WHEEL_M):
        self.wheel_m = wheel_m
        self._state: dict[tuple, _Delta] = {}

    def _delta(self, kind: str, devtype: int, devnum: int) -> _Delta:
        key = (kind, devtype, devnum)
        if key not in self._state:
            self._state[key] = _Delta()
        return self._state[key]

    def decode(self, devtype: int, devnum: int, p: bytes, ts: float) -> list[Sample]:
        if len(p) < 8:
            return []
        out: list[Sample] = []

        def add(metric, value, unit):
            out.append(Sample(ts, devtype, devnum, metric, round(value, 2), unit))

        if devtype == 120:  # Heart rate: computed bpm in byte 7
            if p[7] > 0:
                add("heart_rate", p[7], "bpm")

        elif devtype == 11:  # Bike power, standard power page 0x10
            if p[0] == 0x10:
                add("power", _u16(p, 6), "W")
                if p[3] != 0xFF:
                    add("cadence", p[3], "rpm")

        elif devtype == 121:  # Combined speed & cadence
            self._cadence(add, devtype, devnum, _u16(p, 0), _u16(p, 2))
            self._speed(add, devtype, devnum, _u16(p, 4), _u16(p, 6))

        elif devtype == 122:  # Cadence only
            self._cadence(add, devtype, devnum, _u16(p, 0), _u16(p, 2))

        elif devtype == 123:  # Speed only
            self._speed(add, devtype, devnum, _u16(p, 4), _u16(p, 6))

        return out

    def _cadence(self, add, devtype, devnum, t, r):
        res = self._delta("cad", devtype, devnum).step(t, r)
        if res is not None:
            seconds, dr = res
            add("cadence", dr * 60.0 / seconds, "rpm")

    def _speed(self, add, devtype, devnum, t, r):
        res = self._delta("spd", devtype, devnum).step(t, r)
        if res is not None:
            seconds, dr = res
            add("speed", dr * self.wheel_m / seconds * 3.6, "km/h")
