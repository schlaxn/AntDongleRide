"""Gear deduction and cleaning of speed/cadence data.

The roller is a fixed-speed contact (almost no slip), so for a constant gear:
    speed = cadence * gear_ratio * wheel_circumference * 60 / 1000   (km/h)
gear_ratio = wheel revolutions per crank revolution = chainring / cog.
Development (m per crank revolution) = gear_ratio * wheel_circumference.

Samples are only trusted when the cadence and speed are both clearly moving, because
the ratio is noisy at low speed and at a standstill.
"""
import statistics

MIN_CADENCE = 40.0   # rpm
MIN_SPEED = 8.0      # km/h
TOLERANCE = 0.15     # relative mismatch before a speed sample is replaced
MAX_RATIO = 5.0      # largest realistic ratio (50/11 is about 4.55)
MIN_RATIO = 0.6      # smallest realistic ratio (e.g. 34/50 is about 0.7)


def _num(x) -> float:
    """Missing sensor values (None, blank CSV cells) count as zero."""
    return 0.0 if x in (None, "") else float(x)


def gear_ratio(speed_kmh, cadence_rpm, wheel_m: float) -> float | None:
    speed_kmh, cadence_rpm = _num(speed_kmh), _num(cadence_rpm)
    if cadence_rpm < MIN_CADENCE or speed_kmh < MIN_SPEED or wheel_m <= 0:
        return None
    return (speed_kmh / 3.6) * 60.0 / (cadence_rpm * wheel_m)


def predicted_speed(cadence_rpm: float, ratio: float, wheel_m: float) -> float:
    return cadence_rpm * ratio * wheel_m * 60.0 / 1000.0


def estimate_gear(rows: list[dict], wheel_m: float) -> float | None:
    """Robust gear for a stretch of ride: median of plausible per-sample ratios."""
    ratios = []
    for r in rows:
        g = gear_ratio(r.get("speed_kmh"), r.get("cadence_rpm"), wheel_m)
        if g is not None and MIN_RATIO <= g <= MAX_RATIO:
            ratios.append(g)
    return round(statistics.median(ratios), 3) if ratios else None


def clean_rows(rows: list[dict], wheel_m: float, window: int = 15) -> list[dict]:
    """Return copies of 1 Hz rows with cleaned speed/cadence and a `cleaned` flag.

    Per row: take the gear from the median of the surrounding `window` seconds. If the
    recorded speed disagrees with cadence * gear by more than TOLERANCE, the speed is
    replaced by the predicted value. A zero cadence while moving is replaced too.
    """
    out = []
    n = len(rows)
    for i, r in enumerate(rows):
        lo, hi = max(0, i - window // 2), min(n, i + window // 2 + 1)
        g = estimate_gear(rows[lo:hi], wheel_m)
        c = dict(r)
        c["gear_ratio"] = g
        c["cleaned"] = ""
        if g is not None:
            if _num(c.get("cadence_rpm")) <= 0 and _num(c.get("speed_kmh")) >= MIN_SPEED:
                c["cadence_rpm"] = round(_num(c["speed_kmh"]) * 1000 / (60 * g * wheel_m))
                c["cleaned"] = "cadence"
            cad = _num(c.get("cadence_rpm"))
            pred = predicted_speed(cad, g, wheel_m) if cad > 0 else None
            spd = _num(c.get("speed_kmh"))
            if pred is not None and spd > 0 and abs(spd - pred) / pred > TOLERANCE:
                c["speed_kmh"] = round(pred, 1)
                c["cleaned"] = (c["cleaned"] + "+speed") if c["cleaned"] else "speed"
        out.append(c)
    return out


# ---- virtual gearing (no cadence sensor) ----
# Assumed drivetrain: two chainrings (34 and 50 teeth) and a 10-speed cassette,
# 11-12-13-14-15-17-19-21-24-28 (confirmed by the user).
# Cross-chain rule: the two largest cogs (24, 28) are not used with the 50, and the two smallest
# cogs (11, 12) are not used with the 34. So 50/24, 50/28, 34/11 and 34/12 never happen.
# The drivetrain is walked one gear at a time along two ladders (chainring, cog), like before:
# going harder (upshifting), easiest first. The 34 ring runs out of cogs at 34/13, then the
# shift goes to the 50 ring at 50/17.
UP_LADDER = [(34, 28), (34, 24), (34, 21), (34, 19), (34, 17), (34, 15), (34, 14), (34, 13),
             (50, 17), (50, 15), (50, 14), (50, 13), (50, 12), (50, 11)]
# going easier (downshifting), hardest first. The 50 ring runs down to 50/21, then the shift
# goes to 34/15 (the next easier 34 gear after 50/21).
DOWN_LADDER = [(50, 11), (50, 12), (50, 13), (50, 14), (50, 15), (50, 17), (50, 19), (50, 21),
               (34, 15), (34, 17), (34, 19), (34, 21), (34, 24), (34, 28)]
# GEARS: every usable gear, sorted by ratio (index used by the ride).
GEARS = sorted(set(UP_LADDER) | set(DOWN_LADDER), key=lambda g: g[0] / g[1])
GEAR_RATIOS = [f / c for f, c in GEARS]
CAD_LO, CAD_HI = 85.0, 100.0
START_GEAR = GEARS.index((34, 19))  # index into GEARS


def cadence_for(v_ms: float, ratio: float, wheel_m: float) -> float:
    """Cadence (rpm) that gives speed v_ms in a gear of the given ratio."""
    return v_ms * 60.0 / (ratio * wheel_m)


def _harder(g):
    if g in UP_LADDER:
        i = UP_LADDER.index(g)
        return UP_LADDER[i + 1] if i + 1 < len(UP_LADDER) else g
    j = DOWN_LADDER.index(g)
    return DOWN_LADDER[j - 1] if j > 0 else g


def _easier(g):
    if g in DOWN_LADDER:
        j = DOWN_LADDER.index(g)
        return DOWN_LADDER[j + 1] if j + 1 < len(DOWN_LADDER) else g
    i = UP_LADDER.index(g)
    return UP_LADDER[i - 1] if i > 0 else g


def choose_gear(v_ms: float, current: int, wheel_m: float) -> int:
    """Virtual gearing: while cadence is above CAD_HI shift one gear harder at a time, while it is below
    CAD_LO shift one gear easier at a time, until it is back in the band (or the ladder ends)."""
    if v_ms < 0.5:
        return current  # standing still: no shifting
    g = GEARS[current]
    def cad(gear):
        return cadence_for(v_ms, gear[0] / gear[1], wheel_m)
    if cad(g) > CAD_HI:
        while cad(g) > CAD_HI and _harder(g) != g:
            g = _harder(g)
    elif cad(g) < CAD_LO:
        while cad(g) < CAD_LO and _easier(g) != g:
            g = _easier(g)
    return GEARS.index(g)
