import unittest

from ride import RideState
from workout import Workout


class CadenceSourceTests(unittest.TestCase):
    def _state(self):
        st = RideState(Workout(), 2.105)
        st.status = "preview"
        return st

    def test_combined_sensor_quiet_cadence_is_zero_not_virtual(self):
        # speed is still arriving from a combined sensor, but its cadence went quiet (crank stopped)
        st = self._state()
        now = 1000.0
        st.update("speed", now, 25.0)
        st.advance(now, 0.2, combined_live=True)
        self.assertEqual(st.cadence_rpm_now, 0.0)
        self.assertIsNone(st.virtual_cadence)

    def test_no_cadence_source_uses_virtual_gearing(self):
        st = self._state()
        now = 1000.0
        st.update("speed", now, 25.0)
        st.advance(now, 0.2, combined_live=False)
        self.assertIsNone(st.cadence_rpm_now)
        self.assertIsNotNone(st.virtual_cadence)

    def test_live_cadence_sample_is_used(self):
        st = self._state()
        now = 1000.0
        st.update("speed", now, 25.0)
        st.update("cadence", now, 92.0)
        st.advance(now, 0.2, combined_live=True)
        self.assertEqual(st.cadence_rpm_now, 92.0)
        self.assertIsNone(st.virtual_cadence)


if __name__ == "__main__":
    unittest.main()
