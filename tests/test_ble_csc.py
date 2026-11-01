import unittest

from ble import csc_rates, parse_csc_measurement


class CscParseTests(unittest.TestCase):
    def test_wheel_and_crank_present(self):
        d = parse_csc_measurement(bytes([3, 10, 0, 0, 0, 0x00, 0x04, 5, 0, 0x00, 0x08]))
        self.assertEqual(d, {"wheel_revs": 10, "wheel_t": 1024, "crank_revs": 5, "crank_t": 2048})

    def test_wheel_only(self):
        d = parse_csc_measurement(bytes([1, 7, 0, 0, 0, 0x10, 0x00]))
        self.assertEqual(d, {"wheel_revs": 7, "wheel_t": 16})

    def test_crank_only(self):
        d = parse_csc_measurement(bytes([2, 9, 0, 0x20, 0x00]))
        self.assertEqual(d, {"crank_revs": 9, "crank_t": 32})

    def test_empty_or_truncated(self):
        self.assertEqual(parse_csc_measurement(b""), {})
        self.assertEqual(parse_csc_measurement(bytes([1, 7])), {})


class CscRateTests(unittest.TestCase):
    def test_speed_and_cadence_from_two_measurements(self):
        prev = {"wheel_revs": 10, "wheel_t": 0, "crank_revs": 5, "crank_t": 0}
        cur = {"wheel_revs": 12, "wheel_t": 2048, "crank_revs": 7, "crank_t": 2048}
        r = csc_rates(prev, cur, 2.105)
        self.assertAlmostEqual(r["speed_kmh"], 2 * 2.105 / 2 * 3.6, places=3)   # 2 revs in 2 s
        self.assertAlmostEqual(r["cadence_rpm"], 60.0, places=3)                # 2 crank revs in 2 s

    def test_event_time_wraps_at_64_seconds(self):
        prev = {"crank_revs": 65535, "crank_t": 65000}
        cur = {"crank_revs": 1, "crank_t": 1000}                                 # 1536 ticks later, 2 revs
        r = csc_rates(prev, cur, 2.105)
        self.assertAlmostEqual(r["cadence_rpm"], 2 * 60 / (1536 / 1024), places=3)

    def test_no_rate_without_a_new_event(self):
        prev = {"wheel_revs": 10, "wheel_t": 500}
        cur = {"wheel_revs": 10, "wheel_t": 500}
        self.assertEqual(csc_rates(prev, cur, 2.105), {})

    def test_no_rate_without_a_previous_measurement(self):
        self.assertEqual(csc_rates({}, {"wheel_revs": 3, "wheel_t": 100}, 2.105), {})


if __name__ == "__main__":
    unittest.main()
