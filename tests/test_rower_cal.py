import math
import tempfile
import unittest
from pathlib import Path

from rower.calib import (CAL_WATTS, MAX_STEP_S, MIN_SETTLE_S, CalSession, StepTracker,
                         demand_for_watts, fmt_pace, pace_of_speed, speed_for_watts,
                         watts_for_speed)
from rower.profiles import CODE_PER_MS

FEED_DT = 0.025   # 40 lines per second, as the S4 sends
TICK_DT = 0.5
STROKE_S = 2.0    # 30 strokes per minute: one SS every 2 s
V130 = demand_for_watts(100)   # the 100 W step: 3.8 m/s


def run_tracker(v, code_of_t, t_end):
    """Feed one demanded speed with a code function of time until it locks, times out or t_end."""
    tr = StepTracker(v, 0.0, 0.0)
    t, next_tick, next_stroke = 0.0, 0.0, 0.0
    while t < t_end and tr.locked is None and not tr.timed_out:
        tr.feed(t, code_of_t(t))
        t += FEED_DT
        if t >= next_stroke:
            tr.feed_stroke(next_stroke)
            next_stroke += STROKE_S
        if t >= next_tick:
            tr.tick(t)
            next_tick = t + TICK_DT
    return tr


def nominal(v):
    return v * CODE_PER_MS


def alternating(base, amp=0.005):
    """Per-line swing that averages out over the 3 s window (120 lines, even count)."""
    return lambda t: base * (1.0 + amp * (1 if int(round(t / FEED_DT)) % 2 else -1))


class SpeedFromWattsTests(unittest.TestCase):
    def test_cube_law_at_17_litres(self):
        # W = 1.78 * v**3  ->  v = (W / 1.78) ** (1/3)
        self.assertAlmostEqual(speed_for_watts(100), 3.8297, places=3)
        self.assertAlmostEqual(speed_for_watts(60), 3.2303, places=3)
        self.assertAlmostEqual(speed_for_watts(150), 4.3842, places=3)
        self.assertAlmostEqual(1.78 * speed_for_watts(100) ** 3, 100.0, places=6)

    def test_paces_shown_to_the_rower(self):
        self.assertEqual([fmt_pace(pace_of_speed(demand_for_watts(w))) for w in CAL_WATTS], ["2:12", "2:36", "1:54"])

    def test_demands_rounded_to_a_tenth(self):
        self.assertEqual([demand_for_watts(w) for w in CAL_WATTS], [3.8, 3.2, 4.4])

    def test_watt_levels_in_run_order(self):
        self.assertEqual(CAL_WATTS, (100, 60, 150))


class StepTrackerTests(unittest.TestCase):
    def test_steady_signal_does_not_lock_before_the_settle_gate_plus_hold(self):
        tr = run_tracker(V130, alternating(nominal(V130)), t_end=34.0)
        self.assertIsNone(tr.locked)

    def test_steady_signal_locks_five_seconds_after_the_gate(self):
        tr = run_tracker(V130, alternating(nominal(V130)), t_end=60.0)
        self.assertIsNotNone(tr.locked)
        self.assertGreaterEqual(tr.locked["lock_s"], MIN_SETTLE_S + 5.0 - 1.0)
        self.assertLessEqual(tr.locked["lock_s"], MIN_SETTLE_S + 8.0)
        self.assertAlmostEqual(tr.locked["code"], nominal(V130), delta=nominal(V130) * 0.002)
        self.assertFalse(tr.locked["off_pace"])
        self.assertIsNone(tr.locked["monitor_ms"])            # no reading yet
        self.assertAlmostEqual(tr.locked["v_ref"], V130, places=3)  # demanded stands in

    def test_exponential_approach_locks_later_and_reports_settling(self):
        v = demand_for_watts(150)
        target = nominal(v)
        tr = run_tracker(v, lambda t: target * (1.0 - math.exp(-t / 10.0)) if t > 0 else 1.0,
                         t_end=120.0)
        self.assertIsNotNone(tr.locked)
        self.assertGreater(tr.locked["lock_s"], 40.0)     # still approaching in the first 40 s
        self.assertLess(tr.locked["lock_s"], 90.0)
        self.assertGreater(tr.locked["settle_s"], 25.0)
        # a slow approach can lock while still ~1 % short: the band is relative to the 5 s run
        self.assertAlmostEqual(tr.locked["code"], target, delta=target * 0.02)

    def test_drift_never_locks_and_times_out(self):
        base = nominal(speed_for_watts(60))
        tr = run_tracker(demand_for_watts(60), lambda t: base * (1.0 + 0.01 * t),
                         t_end=MAX_STEP_S + 5)
        self.assertIsNone(tr.locked)
        self.assertTrue(tr.timed_out)

    def test_fast_wobble_outside_the_band_never_locks(self):
        base = nominal(V130)
        # 8 s period, 3 % amplitude: never flat for 5 s. (A 20 s, 1.5 % wobble does lock:
        # the 3 s mean is flat for ~5 s near each peak. See the note in the handover.)
        tr = run_tracker(V130, lambda t: base * (1.0 + 0.03 * math.sin(2 * math.pi * t / 8.0)),
                         t_end=MAX_STEP_S + 5)
        self.assertIsNone(tr.locked)

    def test_wrong_effort_is_flagged_but_still_locks(self):
        tr = run_tracker(V130, alternating(nominal(V130) * 1.3), t_end=60.0)
        self.assertIsNotNone(tr.locked)
        self.assertTrue(tr.locked["off_pace"])


class CalSessionTests(unittest.TestCase):
    def drive_step(self, sess, t, code_for, max_s=150.0):
        """Feed the current step until it locks (the session moves on), times out or max_s passes.
        code_for(sess, t) gives the code at time t. Returns the time reached."""
        idx0 = sess.index
        t_end = t + max_s
        next_tick, next_stroke = t, t
        while t < t_end and sess.status == "running" and sess.index == idx0:
            if sess.tracker.timed_out:
                break
            sess.feed(t, code_for(sess, t))
            t += FEED_DT
            if t >= next_stroke:
                sess.feed_stroke(next_stroke)
                next_stroke += STROKE_S
            if t >= next_tick:
                sess.tick(t)
                next_tick = t + TICK_DT
        return t

    def test_refuses_unknown_levels(self):
        sess = CalSession()
        with self.assertRaises(ValueError):
            sess.start(15, 0.0)
        with self.assertRaises(ValueError):
            sess.start(19, 0.0)

    def test_three_steps_in_order_then_done_with_fit(self):
        sess = CalSession()
        sess.start(17, 0.0)
        t = 0.0
        order = []
        for _ in range(3):
            order.append(sess.tracker.target_w)
            t = self.drive_step(sess, t, lambda s, tt: alternating(nominal(s.tracker.v_target))(tt))
        self.assertEqual(order, [100, 60, 150])
        self.assertEqual(sess.status, "done")
        self.assertEqual([r["target_w"] for r in sess.results], [100, 60, 150])
        fit = sess.fit()
        self.assertEqual(fit["fill_l"], 17)
        self.assertAlmostEqual(fit["code_per_ms"], CODE_PER_MS, delta=0.01)
        self.assertIn("demanded", fit["reference"])

    def test_monitor_reading_replaces_the_demanded_reference(self):
        sess = CalSession()
        sess.start(17, 0.0)
        t = self.drive_step(sess, 0.0, lambda s, tt: nominal(s.tracker.v_target))
        self.assertTrue(sess.snapshot(t)["awaiting_reading"])
        sess.set_reading(3.80, t)               # the monitor shows 3.80 m/s
        res = sess.results[-1]
        self.assertEqual(res["v_ref"], 3.8)
        self.assertAlmostEqual(res["k_code_per_ms"], round(res["code"] / 3.8, 4), places=4)
        self.assertFalse(sess.snapshot(t)["awaiting_reading"])
        with self.assertRaises(ValueError):
            sess.set_reading(50.0, t)

    def test_reading_needs_a_locked_step(self):
        sess = CalSession()
        sess.start(17, 0.0)
        with self.assertRaises(RuntimeError):
            sess.set_reading(3.8, 0.0)

    def test_redo_previous_pops_the_last_step(self):
        sess = CalSession()
        sess.start(17, 0.0)
        t = self.drive_step(sess, 0.0, lambda s, tt: nominal(s.tracker.v_target))
        self.assertEqual(len(sess.results), 1)
        sess.redo_previous(t)
        self.assertEqual(sess.index, 0)
        self.assertEqual(sess.results, [])
        self.assertEqual(sess.tracker.target_w, 100)

    def test_redo_on_first_step_is_refused(self):
        sess = CalSession()
        sess.start(17, 0.0)
        with self.assertRaises(RuntimeError):
            sess.redo_previous(0.0)

    def test_skip_records_and_moves_on(self):
        sess = CalSession()
        sess.start(17, 0.0)
        sess.skip(1.0)
        self.assertEqual(sess.index, 1)
        self.assertTrue(sess.results[0]["skipped"])
        self.assertIsNone(sess.fit())

    def test_finished_session_writes_cal_json_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = CalSession(Path(tmp))
            sess.start(17, 0.0)
            t = 0.0
            for _ in range(3):
                t = self.drive_step(sess, t, lambda s, tt: nominal(s.tracker.v_target))
            self.assertEqual(sess.status, "done")
            folder = Path(sess.saved_to)
            self.assertTrue((folder / "cal.json").exists())
            self.assertTrue((folder / "trace.csv").exists())
            self.assertIn("17L", folder.name)

    def test_snapshot_keys_while_settling(self):
        sess = CalSession()
        sess.start(17, 0.0)
        for i in range(200):
            sess.feed(i * FEED_DT, nominal(V130))
        sess.tick(5.0)
        snap = sess.snapshot(5.0)
        self.assertEqual(snap["type"], "cal")
        self.assertTrue(snap["settling"])
        self.assertEqual(snap["pace"], "2:12")
        self.assertEqual(snap["expected_w"], 100)


if __name__ == "__main__":
    unittest.main()
