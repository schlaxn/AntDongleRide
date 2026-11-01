import unittest

from sensors import SensorRoles, role_status


def sample(metric, devtype, devnum, ts=0.0):
    return {"type": "sample", "metric": metric, "devtype": devtype, "devnum": devnum, "ts": ts, "value": 1}


class AcceptsTests(unittest.TestCase):
    def test_default_accepts_any_device_of_the_right_type(self):
        r = SensorRoles()
        self.assertTrue(r.accepts(sample("heart_rate", 120, 500)))
        self.assertTrue(r.accepts(sample("heart_rate", 120, 777)))

    def test_pinned_device_rejects_others(self):
        r = SensorRoles()
        r.select("heart_rate", 120, 500)
        self.assertTrue(r.accepts(sample("heart_rate", 120, 500)))
        self.assertFalse(r.accepts(sample("heart_rate", 120, 777)))

    def test_bluetooth_watch_selected_by_devnum_minus_one(self):
        r = SensorRoles()
        r.select("heart_rate", 120, -1)
        self.assertTrue(r.accepts(sample("heart_rate", 120, -1)))
        self.assertFalse(r.accepts(sample("heart_rate", 120, 500)))

    def test_speed_and_cadence_can_come_from_one_combined_sensor(self):
        r = SensorRoles()
        r.select("speed", 121, 1200)
        r.select("cadence", 121, 1200)
        self.assertTrue(r.accepts(sample("speed", 121, 1200)))
        self.assertTrue(r.accepts(sample("cadence", 121, 1200)))
        self.assertFalse(r.accepts(sample("speed", 121, 999)))

    def test_wrong_device_type_for_role_rejected(self):
        r = SensorRoles()
        with self.assertRaises(ValueError):
            r.select("heart_rate", 121, 0)
        with self.assertRaises(ValueError):
            r.select("power", 11, 0)

    def test_wrong_metric_rejected(self):
        self.assertFalse(SensorRoles().accepts(sample("power", 11, 5)))


class RoleStatusTests(unittest.TestCase):
    def test_live_when_matching_device_recent(self):
        r = SensorRoles()
        r.select("heart_rate", 120, 500)
        devices = [{"devtype": 120, "devnum": 500, "last": 100.0},
                   {"devtype": 120, "devnum": 777, "last": 100.0}]
        st = role_status(r, devices, now=101.0)
        self.assertTrue(st["heart_rate"]["live"])
        self.assertFalse(st["heart_rate"]["required"])

    def test_not_live_when_only_other_device_sends(self):
        r = SensorRoles()
        r.select("heart_rate", 120, 500)
        devices = [{"devtype": 120, "devnum": 777, "last": 100.0}]
        self.assertFalse(role_status(r, devices, now=100.5)["heart_rate"]["live"])

    def test_stale_device_not_live(self):
        r = SensorRoles()
        devices = [{"devtype": 121, "devnum": 1, "last": 100.0}]
        self.assertFalse(role_status(r, devices, now=110.0)["speed"]["live"])

    def test_cadence_is_optional(self):
        self.assertFalse(role_status(SensorRoles(), [], now=0)["cadence"]["required"])


if __name__ == "__main__":
    unittest.main()
