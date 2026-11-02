import json
import tempfile
import time
import unittest
from pathlib import Path

from rower.engine import RowEngine, RowState
from rower.hub import RowHub
from rower.roles import RowRoles
from workout import parse_shorthand


def short_workout():
    # one 2-second work segment, no warm-up or cool-down
    return parse_shorthand("1-1-2-1", warmup_s=0, cooldown_s=0, between_s=0)


class RowStateTests(unittest.TestCase):
    def test_state_machine(self):
        st = RowState(short_workout())
        self.assertEqual(st.status, "preview")
        st.start()
        self.assertEqual(st.status, "running")
        st.pause()
        self.assertEqual(st.status, "paused")
        st.resume()
        st.stop()
        self.assertEqual(st.status, "finished")
        with self.assertRaises(RuntimeError):
            st.resume()
        with self.assertRaises(RuntimeError):
            RowState(short_workout()).pause()

    def test_clock_and_distance_move_only_while_running(self):
        st = RowState(short_workout())
        t = 100.0
        st.update("speed", t, 4.0)
        st.advance(t, 0.5)                   # preview: physics only
        self.assertEqual(st.elapsed, 0.0)
        self.assertEqual(st.distance_m, 0.0)
        st.start()
        st.advance(t + 0.5, 0.5)
        self.assertAlmostEqual(st.elapsed, 0.5)
        self.assertAlmostEqual(st.distance_m, 2.0)
        st.pause()
        st.advance(t + 1.0, 0.5)
        self.assertAlmostEqual(st.elapsed, 0.5)

    def test_finishes_when_workout_time_runs_out(self):
        st = RowState(short_workout())
        st.start()
        now = 0.0
        for _ in range(30):
            now += 0.1
            st.update("speed", now, 4.0)
            st.advance(now, 0.1)
        self.assertEqual(st.status, "finished")
        self.assertEqual(st.elapsed, st.total_s)

    def test_strokes_and_stale_values(self):
        st = RowState(short_workout())
        st.update("stroke", 10.0, 1)
        st.update("stroke", 11.0, 1)
        self.assertEqual(st.strokes, 2)
        self.assertIsNone(st.value("speed", 10.0))
        st.update("speed", 10.0, 3.0)
        self.assertEqual(st.value("speed", 12.0), 3.0)
        self.assertIsNone(st.value("speed", 20.0))

    def test_snapshot_keys_and_phase(self):
        st = RowState(parse_shorthand("1-1-30-30", warmup_s=5, cooldown_s=0, between_s=0))
        snap = st.snapshot(time.time())
        for key in ("type", "status", "elapsed_s", "total_s", "phase", "phase_left_s",
                    "next_phase", "speed_ms", "split_s", "power_w", "avg_power_w",
                    "stroke_rate_spm", "strokes", "distance_m", "hr_bpm", "target_w",
                    "bots", "bot_counts"):
            self.assertIn(key, snap)
        self.assertEqual(snap["type"], "row")
        self.assertEqual(snap["status"], "preview")
        st.start()
        snap = st.snapshot(time.time())
        self.assertEqual(snap["phase"], "warmup")
        self.assertEqual(snap["target_w"], st.targets["warmup"])

    def test_step_detector_follows_a_jump_and_keeps_steady_stretches_smooth(self):
        st = RowState(short_workout())
        # steady, with a little noise: the display stays at the average
        for i in range(250):
            t = i * 0.1
            st.update("speed", t, 4.0 + (0.1 if i % 2 else -0.1))
        steady = st.snapshot(24.9)["speed_ms"]
        self.assertAlmostEqual(steady, 4.0, delta=0.05)
        # a deliberate step to 5 m/s: the display follows within about 2.5 seconds
        # (the engine publishes 5 times a second, so the detector is run at that rate here)
        for i in range(250, 300):
            t = i * 0.1
            st.update("speed", t, 5.0)
            if i % 2 == 1:
                st.snapshot(t)
        self.assertGreater(st.snapshot(29.9)["speed_ms"], 4.9)
        # once the new effort has settled, the display returns to the slow average
        for i in range(300, 500):
            st.update("speed", i * 0.1, 5.0)
            if i % 2 == 1:
                st.snapshot(i * 0.1)
        self.assertAlmostEqual(st.snapshot(49.9)["speed_ms"], 5.0, delta=0.05)

    def test_split_from_speed(self):
        st = RowState(short_workout())
        now = time.time()
        st.update("speed", now, 5.0)
        self.assertAlmostEqual(st.snapshot(now)["split_s"], 100.0)


class RowEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = RowHub()
        self.engine = RowEngine(self.hub, Path(self.tmp.name), RowRoles(),
                                workout=short_workout(), tick_hz=20.0)

    def tearDown(self):
        self.engine.shutdown()
        self.tmp.cleanup()

    def test_start_stop_writes_one_session_document(self):
        self.engine.start()
        self.assertEqual(self.engine.state.status, "running")
        self.engine.stop()
        self.assertEqual(self.engine.state.status, "finished")
        folders = list(Path(self.tmp.name).iterdir())
        self.assertEqual(len(folders), 1)
        doc = json.loads((folders[0] / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["summary"]["strokes"], 0)
        events = [e["event"] for e in doc["events"]]
        self.assertEqual(events, ["start", "stop"])

    def test_stop_twice_does_not_write_twice(self):
        self.engine.start()
        self.engine.stop()
        self.engine.stop()
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 1)

    def test_start_twice_is_refused(self):
        self.engine.start()
        with self.assertRaises(RuntimeError):
            self.engine.start()
        self.engine.stop()

    def test_pause_resume_do_not_break_the_document(self):
        self.engine.start()
        self.engine.pause()
        self.engine.resume()
        self.engine.stop()
        folder = next(Path(self.tmp.name).iterdir())
        doc = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        self.assertEqual([e["event"] for e in doc["events"]],
                         ["start", "pause", "resume", "stop"])

    def test_engine_publishes_row_snapshots(self):
        q = self.hub.subscribe()
        try:
            found = None
            end = time.time() + 2
            while time.time() < end and found is None:
                try:
                    item = q.get(timeout=0.2)
                except Exception:
                    continue
                if item.get("type") == "row":
                    found = item
            self.assertIsNotNone(found)
            self.assertEqual(found["status"], "preview")
        finally:
            self.hub.unsubscribe(q)


if __name__ == "__main__":
    unittest.main()
