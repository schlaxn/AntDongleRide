import struct
import unittest

from ant.profiles import Decoder


def u16s(*vals):
    return b"".join(struct.pack("<H", v) for v in vals)


class HeartRateTests(unittest.TestCase):
    def test_bpm_is_byte_7(self):
        d = Decoder()
        payload = bytes([0x00, 0xFF, 0xFF, 0x00, 0x10, 0x20, 0x05, 143])
        out = d.decode(120, 42, payload, 0.0)
        self.assertEqual([(s.metric, s.value) for s in out], [("heart_rate", 143)])

    def test_zero_bpm_ignored(self):
        self.assertEqual(Decoder().decode(120, 1, bytes(8), 0.0), [])


class PowerTests(unittest.TestCase):
    def test_standard_power_page(self):
        d = Decoder()
        # page 0x10, event 1, pedal 0xFF, cadence 90, accum power, inst power 250 W
        payload = bytes([0x10, 1, 0xFF, 90]) + struct.pack("<H", 1000) + struct.pack("<H", 250)
        out = {s.metric: s.value for s in d.decode(11, 7, payload, 0.0)}
        self.assertEqual(out, {"power": 250, "cadence": 90})

    def test_other_pages_ignored(self):
        self.assertEqual(Decoder().decode(11, 7, bytes([0x01] + [0] * 7), 0.0), [])


class SpeedCadenceTests(unittest.TestCase):
    def test_cadence_60_rpm_one_rev_per_second(self):
        d = Decoder()
        d.decode(121, 5, u16s(0, 0, 0, 0), 0.0)
        out = {s.metric: s.value for s in d.decode(121, 5, u16s(1024, 1, 0, 0), 1.0)}
        self.assertAlmostEqual(out["cadence"], 60.0)

    def test_rollover_16_bit(self):
        d = Decoder()
        d.decode(122, 9, u16s(65000, 65535, 0, 0), 0.0)
        out = d.decode(122, 9, u16s(488, 0, 0, 0), 1.0)  # 1024 ticks later, 1 rev
        self.assertAlmostEqual(out[0].value, 60.0)

    def test_speed_from_wheel_revs(self):
        d = Decoder(wheel_m=2.0)
        d.decode(123, 3, u16s(0, 0, 0, 0), 0.0)
        out = d.decode(123, 3, u16s(0, 0, 1024, 1), 1.0)  # 1 rev/s, 2 m circumference
        self.assertAlmostEqual(out[0].value, 7.2)  # 2 m/s = 7.2 km/h

    def test_no_new_event_no_sample(self):
        d = Decoder()
        d.decode(122, 9, u16s(100, 1, 0, 0), 0.0)
        self.assertEqual(d.decode(122, 9, u16s(100, 1, 0, 0), 0.1), [])


if __name__ == "__main__":
    unittest.main()
