import unittest

from rower.smoothing import smooth_series


class SmoothingTests(unittest.TestCase):
    def test_steady_values_are_averaged_and_blips_are_ignored(self):
        vals = [100.0] * 20 + [130.0] + [100.0] * 5     # one blip: a single second, not confirmed
        out = smooth_series(vals)
        self.assertAlmostEqual(out[-1], 100.0, places=6)

    def test_a_sustained_change_is_followed_within_the_confirm_time(self):
        vals = [100.0] * 20 + [60.0] * 6
        out = smooth_series(vals)
        self.assertLess(out[21], 100.0)                 # the first 1 s of the change is not yet confirmed
        self.assertAlmostEqual(out[22], 60.0, places=6)  # confirmed after 2 s: follows at once


if __name__ == "__main__":
    unittest.main()
