import unittest

from gear import clean_rows, estimate_gear, gear_ratio, predicted_speed
from power import TrainerPowerModel

WHEEL = 2.105


class PowerModelTests(unittest.TestCase):
    def setUp(self):
        self.m = TrainerPowerModel()

    def test_matches_both_reference_points(self):
        self.assertAlmostEqual(self.m.resistance_w(20), 105, places=6)
        self.assertAlmostEqual(self.m.resistance_w(40), 406, places=6)  # extrapolated point

    def test_exponent_is_about_1_95(self):
        # measured with the Favero Assioma pedals, settled state: exponent 1.95
        self.assertAlmostEqual(self.m.exponent, 1.95, places=2)

    def test_matches_the_measured_curve(self):
        # pedal power at quasi-steady speeds (settled state, 7 Nov 2026), within the fit scatter (~3.5 %)
        for speed, measured_w, tol in ((16, 68, 3), (20, 105, 0.01), (25, 162, 4), (30, 232, 6)):
            with self.subTest(speed=speed):
                self.assertLess(abs(self.m.resistance_w(speed) - measured_w), tol)

    def test_is_below_the_manufacturer_chart(self):
        # the chart reads ~300 W at 30 km/h; the measurement is about 23 % lower (documented, intended)
        ratio = self.m.resistance_w(30) / 300.0
        self.assertGreater(ratio, 0.70)
        self.assertLess(ratio, 0.85)

    def test_zero_speed_zero_power(self):
        self.assertEqual(self.m.resistance_w(0), 0)

    def test_flywheel_inertia_adds_power_when_accelerating(self):
        steady = self.m.power_w(30, 0.0)
        accel = self.m.power_w(30, 1.0)
        # 0.5 * 1.7 kg * (30/3.6 m/s) * 1 m/s^2 = 21.25 W
        self.assertAlmostEqual(accel - steady, 0.5 * 1.7 * (30 / 3.6) * 1.0, places=6)

    def test_power_never_negative_when_decelerating(self):
        self.assertGreaterEqual(self.m.power_w(5, -5.0), 0)


class GearTests(unittest.TestCase):
    def test_gear_ratio_from_speed_and_cadence(self):
        # 4.0 wheel revs per crank rev at 90 rpm: speed = 90*4*2.105*60/1000 = 45.5 km/h
        self.assertAlmostEqual(gear_ratio(45.4, 90, WHEEL), 4.0, places=1)

    def test_no_ratio_at_low_cadence_or_speed(self):
        self.assertIsNone(gear_ratio(30, 20, WHEEL))
        self.assertIsNone(gear_ratio(3, 90, WHEEL))

    def test_predicted_speed_inverts_ratio(self):
        self.assertAlmostEqual(predicted_speed(90, 4.0, WHEEL), 45.5, places=1)

    def test_estimate_gear_ignores_outliers(self):
        rows = [{"speed_kmh": 30.0, "cadence_rpm": 90} for _ in range(20)]
        rows.append({"speed_kmh": 300.0, "cadence_rpm": 90})  # sensor glitch
        g = estimate_gear(rows, WHEEL)
        self.assertAlmostEqual(g, gear_ratio(30, 90, WHEEL), places=3)

    def test_clean_rows_replaces_bad_speed(self):
        good = {"speed_kmh": 30.0, "cadence_rpm": 90, "elapsed_s": 0}
        rows = [dict(good, elapsed_s=i) for i in range(20)]
        rows[10] = {"speed_kmh": 80.0, "cadence_rpm": 90, "elapsed_s": 10}
        cleaned = clean_rows(rows, WHEEL)
        self.assertIn("speed", cleaned[10]["cleaned"])
        self.assertAlmostEqual(cleaned[10]["speed_kmh"], 30.0, delta=0.5)
        self.assertEqual(cleaned[0]["cleaned"], "")

    def test_clean_rows_fills_missing_cadence(self):
        rows = [{"speed_kmh": 30.0, "cadence_rpm": 90, "elapsed_s": i} for i in range(20)]
        rows[5]["cadence_rpm"] = 0
        cleaned = clean_rows(rows, WHEEL)
        self.assertIn("cadence", cleaned[5]["cleaned"])
        self.assertAlmostEqual(cleaned[5]["cadence_rpm"], 90, delta=1)


if __name__ == "__main__":
    unittest.main()
