"""Turns WaterRower S4 broadcast lines into samples (speed, power, split, stroke rate, strokes).

Decoding (fitted against the 17.0 L monitor readings, see rower/measurements/):

  v [m/s]  = code / CODE_PER_MS        code = decimal value of the "P0x" byte
  W        = WATT_CONST * v**3         the user's cube law (17.0 L, v in m/s)

CODE_PER_MS = 2.326 is a least-squares fit through the origin on seven 17 L monitor
readings. The physical unit behind the code is not identified; it is an empirical scale.

The fill-level setting only changes the monitor's displayed power (about 1.07 at 19 L),
so the displayed power sample is scaled by FILL_POWER_FACTOR. The speed sample is not.

Samples carry devtype/devnum (ROWER_DEVTYPE, ROWER_DEVNUM) so the same role and
recorder code that handles ANT samples can take them unchanged.
"""
from dataclasses import dataclass

from .protocol import PULSE, STROKE_START, parse_line

CODE_PER_MS = 2.326    # stream code per m/s (fitted, 17.0 L)
WATT_CONST = 1.78      # cube-law constant, W per (m/s)^3 at 17.0 L
DEFAULT_FILL_L = 17
FILL_POWER_FACTOR = {17: 1.00, 19: 1.07}   # displayed-power factor per fill setting
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


def fill_factor(litres: int) -> float:
    """Displayed-power factor for a fill setting. Unknown settings count as 1.0."""
    return FILL_POWER_FACTOR.get(int(litres), 1.0)


def split_from_speed(v_ms: float) -> float | None:
    """Seconds per 500 m, or None if not moving."""
    if v_ms <= 0:
        return None
    return 500.0 / v_ms


class Decoder:
    """Stateful: smooths the per-line pulse code over a short rolling window (it swings
    within a single stroke), counts strokes and times them from consecutive SS events."""

    def __init__(self, code_per_ms: float = CODE_PER_MS, watt_const: float = WATT_CONST,
                 smoothing_window: int = 20, fill_litres: int = DEFAULT_FILL_L):
        self.code_per_ms = code_per_ms
        self.watt_const = watt_const
        self.fill_litres = fill_litres
        self._codes: list[int] = []
        self._window = smoothing_window
        self._last_stroke_ts: float | None = None

    def decode_line(self, line: str, ts: float) -> list[Sample]:
        kind, value = parse_line(line)
        out: list[Sample] = []

        if kind == PULSE and value is not None:
            self._codes.append(value)
            if len(self._codes) > self._window:
                del self._codes[0]
            mean_code = sum(self._codes) / len(self._codes)
            v = speed_from_code(mean_code, self.code_per_ms)
            out.append(Sample(ts, "speed", round(v, 3), "m/s"))
            watts = power_from_speed(v, self.watt_const) * fill_factor(self.fill_litres)
            out.append(Sample(ts, "power", round(watts, 1), "W"))
            split = split_from_speed(v)
            if split is not None:
                out.append(Sample(ts, "split_500m", round(split, 1), "s"))

        elif kind == STROKE_START:
            out.append(Sample(ts, "stroke", 1, "count"))
            if self._last_stroke_ts is not None:
                dt = ts - self._last_stroke_ts
                if 0 < dt < 10:  # ignore absurd gaps (session start, a long pause)
                    out.append(Sample(ts, "stroke_rate", round(60.0 / dt, 1), "spm"))
            self._last_stroke_ts = ts

        # STROKE_END, PING, OTHER: nothing decoded
        return out
