import re
import unittest
from datetime import datetime
from pathlib import Path

from rower.profiles import Decoder, fill_factor, power_from_speed, speed_from_code
from rower.protocol import parse_line

CAPTURES = Path(__file__).resolve().parent.parent / "rower" / "measurements" / "captures"


def load_lines(name):
    if not (CAPTURES / name).exists():
        raise unittest.SkipTest("private capture data not in this checkout")
    out = []
    for raw in (CAPTURES / name).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s+(\d\d:\d\d:\d\d\.\d+)\s+line: (\S+)$", raw)
        if m:
            out.append((datetime.strptime(m.group(1), "%H:%M:%S.%f"), m.group(2)))
    return out


class ParseLineTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(parse_line("SS")[0], "stroke_start")
        self.assertEqual(parse_line("SE")[0], "stroke_end")
        self.assertEqual(parse_line("PING")[0], "ping")
        self.assertEqual(parse_line("P0A"), ("pulse", 10))
        self.assertEqual(parse_line("P10"), ("pulse", 16))
        self.assertEqual(parse_line("AKR")[0], "other")


class DecoderTests(unittest.TestCase):
    def test_code_to_speed_and_power_formulas(self):
        self.assertAlmostEqual(speed_from_code(2.326), 1.0, places=6)
        self.assertAlmostEqual(power_from_speed(2.0), 1.78 * 8.0, places=6)

    def test_fill_factor(self):
        self.assertEqual(fill_factor(17), 1.0)
        self.assertAlmostEqual(fill_factor(19), 1.07)
        self.assertEqual(fill_factor(15), 1.0)   # not yet calibrated

    def test_pulse_emits_speed_power_and_split(self):
        d = Decoder()
        out = d.decode_line("P0A", 1.0)
        metrics = {s.metric for s in out}
        self.assertEqual(metrics, {"code", "speed", "power", "split_500m"})
        speed = next(s for s in out if s.metric == "speed")
        self.assertEqual((speed.devtype, speed.devnum), (200, 1))

    def test_stroke_counts_and_rate(self):
        d = Decoder()
        first = d.decode_line("SS", 10.0)
        self.assertEqual([s.metric for s in first], ["stroke"])
        second = d.decode_line("SS", 11.5)
        self.assertEqual({s.metric for s in second}, {"stroke", "stroke_rate"})
        rate = next(s for s in second if s.metric == "stroke_rate")
        self.assertAlmostEqual(rate.value, 40.0, places=1)

    def test_long_gap_gives_no_rate(self):
        d = Decoder()
        d.decode_line("SS", 0.0)
        out = d.decode_line("SS", 60.0)
        self.assertEqual([s.metric for s in out], ["stroke"])

    def test_replay_100w_19L_is_within_10_percent_of_monitor(self):
        """The 19 L steady 100 W window from level19_test5 decodes to about the monitor's 100 W."""
        lines = load_lines("level19_test5.txt")
        base = lines[0][0]
        d = Decoder(fill_litres=19)
        ws = datetime.strptime("17:55:29.000", "%H:%M:%S.%f")
        we = datetime.strptime("17:55:49.000", "%H:%M:%S.%f")
        watts = []
        for t, line in lines:
            for s in d.decode_line(line, (t - base).total_seconds()):
                if s.metric == "power" and ws <= t < we:
                    watts.append(s.value)
        self.assertGreater(len(watts), 100)
        mean = sum(watts) / len(watts)
        self.assertLess(abs(mean - 100) / 100, 0.10, f"decoded mean {mean:.1f} W")

    def test_replay_counts_strokes_in_the_180W_capture(self):
        lines = load_lines("rev_test5.txt")
        base = lines[0][0]
        d = Decoder()
        strokes = 0
        for t, line in lines:
            strokes += sum(1 for s in d.decode_line(line, (t - base).total_seconds())
                           if s.metric == "stroke")
        self.assertEqual(strokes, 24)   # SS count in that capture


if __name__ == "__main__":
    unittest.main()
