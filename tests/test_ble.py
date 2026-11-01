import unittest

from ble import parse_hr_measurement


class HeartRateMeasurementTests(unittest.TestCase):
    def test_8bit_value(self):
        self.assertEqual(parse_hr_measurement(bytes([0x00, 72])), 72)

    def test_16bit_value(self):
        self.assertEqual(parse_hr_measurement(bytes([0x01, 0x2C, 0x01])), 300)

    def test_zero_or_empty_ignored(self):
        self.assertIsNone(parse_hr_measurement(bytes([0x00, 0])))
        self.assertIsNone(parse_hr_measurement(b""))


if __name__ == "__main__":
    unittest.main()
