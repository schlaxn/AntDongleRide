"""Low-level parsing of the WaterRower S4's broadcast lines.

This is empirically reverse-engineered from USB captures (see ROWER_HANDOVER.md for the
Phase 0 test log that derived it) -- it is not transcribed from a published WaterRower
spec, since no such spec turned up. If a line doesn't match anything here it is passed
through as OTHER so nothing is silently dropped; check the raw capture if something
looks wrong.

Lines seen so far, at a roughly constant ~40 lines/second status-update rate
regardless of effort (so this is a periodic report, not one event per physical pulse):
    SS        stroke start
    SE        stroke end
    P01..P10+ a flywheel-speed-like code, two hex digits (decimal value rises
              smoothly with rowing effort; see rower/profiles.py for the calibration)
    PING      once-per-second keepalive, no data
    AKR       seen once at startup, meaning unknown
"""
import re

PULSE_RE = re.compile(r"^P([0-9A-Fa-f]{2})$")

STROKE_START = "stroke_start"   # "SS"
STROKE_END = "stroke_end"       # "SE"
PULSE = "pulse"                  # "P01".."PFF" -- decimal value is in the returned tuple
PING = "ping"                    # "PING" keepalive
OTHER = "other"                  # anything unrecognised (value is None)


def parse_line(line: str) -> tuple[str, int | None]:
    """Classify one decoded ASCII line (as produced by rower.serialdev.split_lines).
    Returns (kind, value); value is only set for PULSE, as the decimal 0-255 byte."""
    line = line.strip()
    if line == "SS":
        return STROKE_START, None
    if line == "SE":
        return STROKE_END, None
    if line == "PING":
        return PING, None
    m = PULSE_RE.match(line)
    if m:
        return PULSE, int(m.group(1), 16)
    return OTHER, None
