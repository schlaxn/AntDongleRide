import unittest

from physics import RoadPhysics


class RoadPhysicsTests(unittest.TestCase):
    def setUp(self):
        self.p = RoadPhysics()

    def test_flat_power_gives_plausible_speed(self):
        # ~150 W on the flat for an 82 kg road bike is roughly 30 km/h
        self.assertAlmostEqual(self.p.steady_speed(150, 0) * 3.6, 30.4, delta=0.5)

    def test_climb_is_slower_than_flat(self):
        self.assertLess(self.p.steady_speed(250, 8), self.p.steady_speed(250, 0))

    def test_descent_is_faster_than_flat(self):
        self.assertGreater(self.p.steady_speed(120, -6), self.p.steady_speed(120, 0))

    def test_no_power_on_flat_decelerates_to_rest(self):
        v = 8.0
        for _ in range(200):
            v = self.p.step(v, 0.0, 0.0, 1.0)
        self.assertLess(v, 0.5)

    def test_speed_never_negative(self):
        v = 0.0
        for _ in range(50):
            v = self.p.step(v, 0.0, 10.0, 1.0)  # steep climb, no power: must stop, not reverse
            self.assertGreaterEqual(v, 0.0)

    def test_starting_from_rest_accelerates(self):
        v = self.p.step(0.0, 200.0, 0.0, 5.0)
        self.assertGreater(v, 1.0)


if __name__ == "__main__":
    unittest.main()
