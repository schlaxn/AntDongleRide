import unittest

from gear import CAD_HI, CAD_LO, GEAR_RATIOS, GEARS, START_GEAR, cadence_for, choose_gear

WHEEL = 2.105


class VirtualGearingTests(unittest.TestCase):
    def test_usable_gears_follow_the_rules(self):
        # 10-speed cassette: 11 12 13 14 15 17 19 21 24 28
        self.assertEqual(len(GEAR_RATIOS), 16)
        self.assertEqual(GEARS[0], (34, 28))     # easiest
        self.assertEqual(GEARS[-1], (50, 11))    # hardest
        self.assertEqual(GEAR_RATIOS, sorted(GEAR_RATIOS))
        # the two largest cogs are not used with the 50, the two smallest not with the 34
        for forbidden in [(50, 24), (50, 28), (34, 11), (34, 12), (50, 30), (50, 34)]:
            self.assertNotIn(forbidden, GEARS)
        cassette = {11, 12, 13, 14, 15, 17, 19, 21, 24, 28}
        for ring, cog in GEARS:
            self.assertIn(cog, cassette)

    def test_upshift_from_34_15_steps_to_34_14(self):
        # 105 rpm in 34/15: one cog harder (34/14) brings it to about 98 rpm, in band
        cur = GEARS.index((34, 15))
        v = 105 * (34 / 15) * WHEEL / 60
        self.assertEqual(GEARS[choose_gear(v, cur, WHEEL)], (34, 14))

    def test_upshift_from_34_13_goes_to_50_17(self):
        # 105 rpm in 34/13: the 34 ring is out of cogs, so the shift goes to the 50 ring at 50/17
        cur = GEARS.index((34, 13))
        v = 105 * (34 / 13) * WHEEL / 60
        self.assertEqual(GEARS[choose_gear(v, cur, WHEEL)], (50, 17))

    def test_downshift_from_50_21_goes_to_34_19(self):
        # 65 rpm in 50/21: too low a cadence; 34/15 and 34/17 are still too low, 34/19 is in band
        cur = GEARS.index((50, 21))
        v = 65 * (50 / 21) * WHEEL / 60
        self.assertEqual(GEARS[choose_gear(v, cur, WHEEL)], (34, 19))

    def test_upshift_from_34_19_steps_to_34_17(self):
        cur = GEARS.index((34, 19))
        v = 105 * (34 / 19) * WHEEL / 60          # 105 rpm in 34/19: one gear harder
        self.assertEqual(GEARS[choose_gear(v, cur, WHEEL)], (34, 17))

    def test_cadence_stays_in_band_over_a_speed_range(self):
        gear = START_GEAR
        # below about 14 km/h even 34/28 (the easiest gear) gives under 85 rpm, so the band starts at 14
        for kmh in (14, 16, 20, 24, 28, 32, 36, 30, 22, 18):
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
