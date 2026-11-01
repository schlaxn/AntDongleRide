import csv
import json
import tempfile
import unittest
from pathlib import Path

from recorder import RIDE_COLUMNS, Recorder
from ride import STALE_S, RideState
from workout import parse_shorthand


def fresh_state(shorthand="1-2-10-5", **kw):
    kw.setdefault("warmup_s", 0)
    kw.setdefault("cooldown_s", 0)
    kw.setdefault("between_s", 0)
    return RideState(parse_shorthand(shorthand, **kw))


class DistanceTests(unittest.TestCase):
    def test_distance_is_speed_times_time(self):
        st = fresh_state()
        st.start()
        st.update("speed", 100.0, 36.0)          # 36 km/h = 10 m/s
        for i in range(10):
            st.advance(100.0 + i * 0.1, 0.1)      # one second in total
        self.assertAlmostEqual(st.distance_m, 10.0, places=6)
        self.assertAlmostEqual(st.elapsed, 1.0, places=6)

    def test_speed_gap_up_to_3s_holds_last_speed(self):
        st = fresh_state()
        st.start()
        st.update("speed", 100.0, 36.0)
        st.advance(100.0 + 2.5, 1.0)             # 2.5 s gap: still held
        self.assertGreater(st.distance_m, 9.0)
        self.assertAlmostEqual(st.elapsed, 1.0)  # clock still runs

    def test_speed_gap_over_3s_counts_as_zero(self):
        st = fresh_state()
        st.start()
        st.update("speed", 100.0, 36.0)
        st.advance(100.0 + 3.5, 1.0)             # 3.5 s gap: no longer held
        self.assertEqual(st.power_w, 0.0)        # the speed used is zero, so no rider power
        self.assertAlmostEqual(st.elapsed, 1.0)

    def test_no_speed_or_cadence_for_30s_stops_the_bike(self):
        st = fresh_state()
        st.start()
        st.update("speed", 100.0, 36.0)
        st.advance(100.0 + 31.0, 1.0)
        self.assertEqual(st.v_virtual, 0.0)


class ClockTests(unittest.TestCase):
    def test_pause_freezes_clock_and_distance(self):
        st = fresh_state()
        st.start()
        st.update("speed", 0.0, 36.0)
        st.advance(0.0, 2.0)
        st.pause()
        st.advance(2.0, 5.0)
        self.assertAlmostEqual(st.elapsed, 2.0)
        st.resume()
        st.advance(7.0, 1.0)
        self.assertAlmostEqual(st.elapsed, 3.0)

    def test_finish_at_total(self):
        st = fresh_state("1-1-10-5", warmup_s=0, cooldown_s=0)
        st.start()
        st.advance(0.0, st.workout.total_s + 50)
        self.assertEqual(st.status, "finished")
        self.assertEqual(st.elapsed, st.workout.total_s)

    def test_illegal_transitions_raise(self):
        st = fresh_state()
        with self.assertRaises(RuntimeError):
            st.pause()
        st.start()
        with self.assertRaises(RuntimeError):
            st.start()


class PhaseAndGradientTests(unittest.TestCase):
    def test_snapshot_reports_phase_and_next(self):
        st = fresh_state("1-2-10-5")
        st.start()
        snap = st.snapshot(0.0)
        self.assertEqual(snap["phase"], "work")
        self.assertEqual(snap["next_phase"], "recover")
        self.assertEqual(snap["block"], 1)
        self.assertEqual(snap["rep"], 1)

    def test_altitude_rises_on_climb(self):
        st = fresh_state("1-1-10-5")
        st.start()
        st.update("speed", 0.0, 36.0)            # 10 m/s
        st.advance(0.0, 5.0)                     # 5 s on the first work segment (+8 %)
        self.assertAlmostEqual(st.altitude_m, 10 * 5 * st.gradient_at(0) / 100)
        self.assertGreater(st.altitude_m, 0)


class NoCadenceTests(unittest.TestCase):
    """Speed + heart rate only: no cadence sensor at all."""

    def setUp(self):
        self.st = fresh_state("1-1-20-10")
        self.st.start()
        self.st.update("speed", 0.0, 30.0)
        self.st.update("heart_rate", 0.0, 140)
        self.st.advance(0.0, 1.0)

    def test_missing_cadence_is_none_not_zero(self):
        snap = self.st.snapshot(0.0)
        self.assertIsNone(snap["cadence_rpm"])
        self.assertEqual(snap["hr_bpm"], 140)

    def test_gear_unknown_without_cadence(self):
        self.assertIsNone(self.st.snapshot(0.0)["gear_ratio"])

    def test_power_still_estimated_from_speed(self):
        self.assertGreater(self.st.snapshot(0.0)["power_w"], 0)

    def test_stale_cadence_is_none(self):
        st = fresh_state("1-1-20-10")
        st.start()
        st.update("cadence", 0.0, 90)
        self.assertEqual(st.value("cadence", 0.0 + STALE_S + 1), None)

    def test_cleaning_and_recording_tolerate_missing_cadence(self):
        from gear import clean_rows
        rows = [{"elapsed_s": i, "speed_kmh": 30.0, "cadence_rpm": None} for i in range(20)]
        cleaned = clean_rows(rows, 2.105)
        self.assertIsNone(cleaned[0]["gear_ratio"])
        self.assertEqual(cleaned[0]["cleaned"], "")
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(Path(tmp), 1_700_000_000.0)
            rec.row(self.st.row(0.0))
            rec.close({"settings": {"wheel_circumference_m": 2.105}})
            with open(rec.dir / "ride_clean.csv", encoding="utf-8") as f:
                self.assertEqual(len(list(csv.reader(f))), 2)


class RecorderTests(unittest.TestCase):
    def test_writes_csv_rows_and_session_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(Path(tmp), 1_700_000_000.0)
            st = fresh_state()
            st.start()
            st.update("heart_rate", 0.0, 120)
            st.update("speed", 0.0, 30.0)
            st.update("cadence", 0.0, 90)
            st.advance(0.0, 1.0)
            rec.row(st.row(0.0))
            rec.sample({"ts": 0.0, "devtype": 121, "devnum": 1, "metric": "speed", "value": 30, "unit": "km/h"})
            rec.close({"workout": {"shorthand": "1-2-10-5"}, "events": [], "summary": {"duration_s": 1}})

            with open(rec.dir / "ride.csv", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], RIDE_COLUMNS)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][RIDE_COLUMNS.index("phase")], "work")
            self.assertEqual(rows[1][RIDE_COLUMNS.index("hr_bpm")], "120")

            with open(rec.dir / "samples.csv", encoding="utf-8") as f:
                self.assertEqual(len(list(csv.reader(f))), 2)

            doc = json.loads((rec.dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["summary"]["avg_hr_bpm"], 120)
            self.assertEqual(doc["summary"]["duration_s"], 1)
            self.assertEqual(doc["workout"]["shorthand"], "1-2-10-5")


if __name__ == "__main__":
    unittest.main()
