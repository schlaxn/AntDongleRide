import unittest

import bots
from bots import ARCHETYPES, Bot, Traffic
from physics import RoadPhysics
from ride import RideState
from workout import Workout, parse_shorthand

TOTAL = 3255   # the 3-13-30-15 workout with the last recovery skipped: 54:15


def _run_ride(traffic: Traffic, total: int = TOTAL):
    """Synthetic rider: 273 W on work, 147 W on recovery, 123 W warm-up, 119 W cool-down."""
    phys = RoadPhysics()
    rd, rv = 0.0, 0.0
    peak = 0
    for sec in range(total):
        if sec < 600:
            kind, pw = "warmup", 123.0
        elif sec >= total - 600:
            kind, pw = "cooldown", 119.0
        else:
            kind = "work" if (sec - 600) % 45 < 30 else "recover"
            pw = 273.0 if kind == "work" else 147.0
        rv = phys.step(rv, pw, 0.0, 1.0)
        rd += rv
        traffic.update(1.0, sec + 1, rd, rv, pw, kind, 0.0)
        peak = max(peak, len(traffic.bots))
    return rd, peak


class ScheduleTests(unittest.TestCase):
    def test_eight_encounters_per_minute(self):
        t = Traffic(TOTAL, seed=1)
        self.assertAlmostEqual(len(t.schedule), TOTAL / 60 * bots.RATE_PER_MIN, delta=1)

    def test_same_seed_same_schedule(self):
        self.assertEqual(Traffic(TOTAL, seed=7).schedule, Traffic(TOTAL, seed=7).schedule)


class FullRideTests(unittest.TestCase):
    def test_about_100_riders_met_and_split_60_40(self):
        t = Traffic(TOTAL, seed=1)
        _, peak = _run_ride(t)
        c = t.counts
        self.assertGreaterEqual(c["met"], 100)
        self.assertLessEqual(peak, bots.MAX_ACTIVE)
        decided = c["passed"] + c["passed_by"]
        self.assertGreater(decided, 100)
        share = c["passed"] / decided
        self.assertTrue(0.55 <= share <= 0.65, f"rider passed {share:.2f} of the riders")

    def test_no_rider_drifts_far_out_of_reach(self):
        t = Traffic(TOTAL, seed=2)
        phys = RoadPhysics()
        rd, rv = 0.0, 0.0
        for sec in range(TOTAL):
            kind = "warmup" if sec < 600 else "cooldown" if sec >= TOTAL - 600 else (
                "work" if (sec - 600) % 45 < 30 else "recover")
            pw = {"warmup": 123.0, "cooldown": 119.0, "work": 273.0, "recover": 147.0}[kind]
            rv = phys.step(rv, pw, 0.0, 1.0)
            rd += rv
            t.update(1.0, sec + 1, rd, rv, pw, kind, 0.0)
            for b in t.bots:
                self.assertLessEqual(abs(b.d - rd), bots.DROP_AHEAD_M + 61)


class ArchetypeTests(unittest.TestCase):
    def _bot(self, arch, **kw):
        base = dict(id=1, name="x", arch=arch, role="slower", jersey=0, lane=-2.25, d=0.0, v=8.0,
                    factor=1.0, spawned_at=0.0, surge=None, side=1)
        base.update(kw)
        return Bot(**base)

    def test_diesel_ignores_the_intervals(self):
        t = Traffic(TOTAL, seed=3)
        b = self._bot("Diesel")
        self.assertAlmostEqual(t._power(b, "work", 0.0, 100.0), t._power(b, "recover", 0.0, 100.0))

    def test_attacker_harder_on_work_than_recovery(self):
        t = Traffic(TOTAL, seed=3)
        b = self._bot("Attacker")
        self.assertGreater(t._power(b, "work", 0.0, 100.0), t._power(b, "recover", 0.0, 100.0) * 1.3)

    def test_climber_gains_on_gradients(self):
        t = Traffic(TOTAL, seed=3)
        b = self._bot("Climber")
        self.assertGreater(t._power(b, "work", 8.0, 100.0), t._power(b, "work", 0.0, 100.0))

    def test_every_archetype_has_a_jersey_and_nick(self):
        for name, a in ARCHETYPES.items():
            self.assertIn("nick", a, name)
            self.assertIsInstance(a["jersey"], int, name)


class PauseTests(unittest.TestCase):
    def test_paused_ride_freezes_riders_and_resume_continues(self):
        st = RideState(parse_shorthand("1-2-30-15", warmup_s=60, cooldown_s=60, skip_last_recover=False), 2.105)
        st.start()
        now = 1000.0
        for _ in range(200):
            now += 1.0
            st.advance(now, 1.0)
        before = len(st.traffic.bots), dict(st.traffic.counts)
        st.pause()
        for _ in range(600):   # ten minutes of pause
            now += 1.0
            st.advance(now, 1.0)
        self.assertEqual((len(st.traffic.bots), dict(st.traffic.counts)), before)
        st.resume()
        for _ in range(10):
            now += 1.0
            st.advance(now, 1.0)
        self.assertTrue(st.traffic.bots or st.traffic.counts["met"] > 0)

    def test_snapshot_carries_riders_and_counts(self):
        st = RideState(Workout(), 2.105)
        st.start()
        snap = st.snapshot(1000.0)
        self.assertIn("bots", snap)
        self.assertIn("bot_counts", snap)


if __name__ == "__main__":
    unittest.main()
