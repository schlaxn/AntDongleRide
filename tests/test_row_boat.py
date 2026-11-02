import unittest

from rower.boat import BoatPhysics


class BoatPhysicsTests(unittest.TestCase):
    def test_steady_state_matches_monitor_cube_law(self):
        b = BoatPhysics()
        p = 150.0
        v = 0.0
        for _ in range(600):           # 60 s at 0.1 s steps
            v = b.step(v, p, 0.1)
        self.assertAlmostEqual(v, b.steady_speed(p), places=2)
        self.assertAlmostEqual(v, (p / 1.78) ** (1 / 3), places=2)

    def test_steady_speed_closed_form(self):
        b = BoatPhysics()
        self.assertAlmostEqual(b.steady_speed(0), 0.0)
        self.assertAlmostEqual(b.steady_speed(1.78 * 4.0 ** 3), 4.0, places=9)

    def test_coasts_down_and_never_negative(self):
        b = BoatPhysics()
        v = 3.0
        prev = v
        for _ in range(100):
            v = b.step(v, 0.0, 0.5)
            self.assertLessEqual(v, prev)      # drag only slows the boat
            self.assertGreaterEqual(v, 0.0)
            prev = v
        self.assertLess(v, 3.0)

    def test_more_power_means_faster(self):
        b = BoatPhysics()
        slow = b.steady_speed(100)
        fast = b.steady_speed(200)
        self.assertGreater(fast, slow)

    def test_zero_dt_is_a_no_op(self):
        b = BoatPhysics()
        self.assertEqual(b.step(2.5, 150, 0.0), 2.5)

    def test_rejects_non_positive_constants(self):
        with self.assertRaises(ValueError):
            BoatPhysics(mass_kg=0)
        with self.assertRaises(ValueError):
            BoatPhysics(drag_c=-1)


if __name__ == "__main__":
    unittest.main()
