import unittest

from gear import CAD_HI, CAD_LO, GEAR_RATIOS, GEARS, START_GEAR, cadence_for, choose_gear

WHEEL = 2.105


class VirtualGearingTests(unittest.TestCase):
    def test_usable_gears_follow_the_rules(self):
        self.assertEqual(len(GEAR_RATIOS), 19)
        self.assertEqual(GEARS[0], (34, 34))     # easiest
        self.assertEqual(GEARS[-1], (50, 11))    # hardest
        self.assertEqual(GEAR_RATIOS, sorted(GEAR_RATIOS))
        for forbidden in [(50, 30), (50, 34), (34, 13), (34, 12), (34, 11)]:
            self.assertNotIn(forbidden, GEARS)

    def test_upshift_runs_through_small_ring_then_to_50_17(self):
        # at 105 rpm in 34/14 the next harder gear is 50/17, not 50/19 or anything else
        cur = GEARS.index((34, 14))
        v = 105 * (34 / 14) * WHEEL / 60
        self.assertEqual(GEARS[choose_gear(v, cur, WHEEL)], (50, 17))

    def test_downshift_from_50_27_goes_to_34_21(self):
        cur = GEARS.index((50, 27))
        v = 80 * (50 / 27) * WHEEL / 60            # 80 rpm in 50/27: too low a cadence
        self.assertEqual(GEARS[choose_gear(v, cur, WHEEL)], (34, 21))

    def test_upshift_from_34_21_steps_to_34_19(self):
        cur = GEARS.index((34, 21))
        v = 105 * (34 / 21) * WHEEL / 60          # 105 rpm in 34/21: one gear harder
        self.assertEqual(GEARS[choose_gear(v, cur, WHEEL)], (34, 19))

    def test_cadence_stays_in_band_over_a_speed_range(self):
        gear = START_GEAR
        for kmh in (12, 16, 20, 24, 28, 32, 36, 30, 22, 14):
            gear = choose_gear(kmh / 3.6, gear, WHEEL)
            cad = cadence_for(kmh / 3.6, GEAR_RATIOS[gear], WHEEL)
            with self.subTest(kmh=kmh):
                self.assertTrue(CAD_LO <= cad <= CAD_HI, f"{cad:.0f} rpm at {kmh} km/h")

    def test_no_shift_when_in_band(self):
        gear = START_GEAR
        # a speed that gives 92 rpm in the current gear: the gearbox must not shift
        v = 92 * GEAR_RATIOS[gear] * WHEEL / 60
        self.assertEqual(choose_gear(v, gear, WHEEL), gear)

    def test_standing_still_keeps_gear(self):
        self.assertEqual(choose_gear(0.0, 3, WHEEL), 3)

    def test_too_slow_for_any_gear_takes_easiest(self):
        # at 3 km/h even the easiest gear gives a cadence well below 75 rpm
        self.assertEqual(choose_gear(3 / 3.6, 10, WHEEL), 0)


if __name__ == "__main__":
    unittest.main()
