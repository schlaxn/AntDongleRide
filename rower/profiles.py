"""Turns WaterRower S4 broadcast lines into samples (speed, power, split, stroke rate, strokes).

Decoding (fitted against the 17.0 L monitor readings, see rower/measurements/):

  v [m/s]  = code / CODE_PER_MS        code = decimal value of the "P0x" byte
  W        = WATT_CONST * v**3         the user's cube law (17.0 L, v in m/s)

CODE_PER_MS = 2.326 is a least-squares fit through the origin on seven 17 L monitor
readings. The physical unit behind the code is not identified; it is an empirical scale.

The fill-level setting only changes the monitor's displayed power (about 1.07 at 19 L),
so the displayed power sample is scaled by fill_factor(). The speed sample is not.

Samples carry devtype/devnum (ROWER_DEVTYPE, ROWER_DEVNUM) so the same role and
recorder code that handles ANT samples can take them unchanged.
"""
from dataclasses import dataclass
from collections import deque

from .protocol import PULSE, STROKE_END, STROKE_START, parse_line

CODE_PER_MS = 2.326    # stream code per m/s (fitted, 17.0 L)
TICKS_PER_M = 91.8     # ring-segment ticks per metre (3 Nov, four rest-to-rest runs); speed and distance
TICK_WINDOW_S = 1.0    # speed = ticks in the last second, so bursts and absent lines average out
WATT_CONST = 1.78      # cube-law constant, W per (m/s)^3 at 17.0 L
DEFAULT_FILL_L = 17
# Displayed-power factor per fill setting, relative to 17 L (c = 1.78 W per (m/s)^3 there): a straight line
# through 17 L, 1 + 0.054 per litre. Measured on the monitor at a steady speed, c = W / v^3:
# 15 L 1.578 (4.0 m/s, 101 W; 0.6 % below the line), 19 L 1.94 to 1.96 (3.72 m/s, 100 or 101 W; the
# reading was not certain), 20 L 2.077 (3.65 m/s, 101 W; 0.4 % above the line).
FILL_SLOPE_PER_L = 0.054
ROWER_DEVTYPE = 200    # pseudo device type for the S4 in the shared sample stream
ROWER_DEVNUM = 1
SOURCE = "rower"


@dataclass
class Sample:
    ts: float
    metric: str
    value: float
    unit: str
    devtype: int = ROWER_DEVTYPE
    devnum: int = ROWER_DEVNUM

    def to_dict(self) -> dict:
        return {"type": "sample", "source": SOURCE, **self.__dict__}


def speed_from_code(code: float, code_per_ms: float = CODE_PER_MS) -> float:
    """m/s."""
    return code / code_per_ms


def power_from_speed(v_ms: float, watt_const: float = WATT_CONST) -> float:
    """Watts from speed, W = watt_const * v**3 (physical, 17 L)."""
    return watt_const * max(v_ms, 0.0) ** 3


def fill_factor(litres: float) -> float:
    """Displayed-power factor for a fill setting (any number of litres): the straight line through 17 L."""
    return max(0.5, 1.0 + FILL_SLOPE_PER_L * (float(litres) - 17.0))


def split_from_speed(v_ms: float) -> float | None:
    """Seconds per 500 m, or None if not moving."""
    if v_ms <= 0:
        return None
    return 500.0 / v_ms


class Decoder:
    """Stateful. Speed is the ticks counted in the last TICK_WINDOW_S divided by TICKS_PER_M
    (absent lines count as zero ticks). Distance is the ticks since the first stroke divided
    by TICKS_PER_M. Power is updated once per completed stroke (SS to SS), as the monitor does,
    from the ticks counted in that stroke."""

    def __init__(self, ticks_per_m: float = TICKS_PER_M, watt_const: float = WATT_CONST,
                 fill_litres: float = DEFAULT_FILL_L, window_s: float = TICK_WINDOW_S):
        self.ticks_per_m = ticks_per_m
        self.watt_const = watt_const
        self.fill_litres = fill_litres
        self.window_s = window_s
        self._recent: deque = deque()          # (ts, ticks) inside the speed window
        self._cycle_ticks = 0                  # ticks since the last SS
        self._last_ss: float | None = None
        self._first_ss: float | None = None
        self._speeds: list[float] = []           # the speed signal since the last stroke (for power)
        self._distance_ticks = 0

    def decode_line(self, line: str, ts: float) -> list[Sample]:
        kind, value = parse_line(line)
        out: list[Sample] = []

        if kind == PULSE and value is not None:
            out.append(Sample(ts, "code", value, "code"))   # raw per-line tick count, for calibration
            self._recent.append((ts, value))
            while self._recent and ts - self._recent[0][0] >= self.window_s:
                self._recent.popleft()
            ticks = sum(c for _, c in self._recent)
            v = ticks / self.window_s / self.ticks_per_m
            out.append(Sample(ts, "speed", round(v, 3), "m/s"))
            self._speeds.append(v)
            split = split_from_speed(v)
            if split is not None:
                out.append(Sample(ts, "split_500m", round(split, 1), "s"))
            self._cycle_ticks += value
            if self._first_ss is not None:
                self._distance_ticks += value
                out.append(Sample(ts, "distance", round(self._distance_ticks / self.ticks_per_m, 2), "m"))

        elif kind == STROKE_START:
            out.append(Sample(ts, "stroke", 1, "count"))
            if self._last_ss is not None:
                dt = ts - self._last_ss
                if 0 < dt < 10:  # ignore absurd gaps (session start, a long pause)
                    out.append(Sample(ts, "stroke_rate", round(60.0 / dt, 1), "spm"))
                    v = self._cycle_ticks / dt / self.ticks_per_m          # this stroke's speed (pace)
                    out.append(Sample(ts, "stroke_speed", round(v, 3), "m/s"))
                    # power from the averaged speed signal over this stroke, given once per stroke
                    if self._speeds:
                        v_avg = sum(self._speeds) / len(self._speeds)
                        watts = power_from_speed(v_avg, self.watt_const) * fill_factor(self.fill_litres)
                        out.append(Sample(ts, "power", round(watts, 1), "W"))
            self._cycle_ticks = 0
            self._speeds = []
            if self._first_ss is None:
                self._first_ss = ts
                self._distance_ticks = 0
            self._last_ss = ts

        elif kind == STROKE_END:
            out.append(Sample(ts, "stroke_end", 1, "count"))   # the drive ends: the animation's finish

        # PING, OTHER: nothing decoded
        return out
