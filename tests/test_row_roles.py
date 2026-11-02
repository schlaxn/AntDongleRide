import unittest

from rower.roles import ROLE_OF_METRIC, RowRoles, role_status


def sample(metric, devtype, devnum=0, ts=10.0):
    return {"type": "sample", "metric": metric, "devtype": devtype, "devnum": devnum,
            "value": 1, "ts": ts}


class RowRolesTests(unittest.TestCase):
    def test_default_accepts_rower_and_any_heart_rate(self):
        r = RowRoles()
        self.assertTrue(r.accepts(sample("speed", 200, 1)))
        self.assertTrue(r.accepts(sample("stroke", 200, 1)))
        self.assertTrue(r.accepts(sample("heart_rate", 120, -1)))   # BLE watch
        self.assertTrue(r.accepts(sample("heart_rate", 120, 7)))

    def test_rejects_wrong_device_type_and_unknown_metric(self):
        r = RowRoles()
        self.assertFalse(r.accepts(sample("speed", 121, 1)))
        self.assertFalse(r.accepts(sample("bogus", 200, 1)))
        self.assertFalse(r.accepts({"metric": "speed"}))

    def test_pinned_device_only(self):
        r = RowRoles()
        r.select("heart_rate", 120, 7)
        self.assertTrue(r.accepts(sample("heart_rate", 120, 7)))
        self.assertFalse(r.accepts(sample("heart_rate", 120, 8)))

    def test_select_validates(self):
        r = RowRoles()
        with self.assertRaises(ValueError):
            r.select("nope", 200, 0)
        with self.assertRaises(ValueError):
            r.select("rower", 120, 0)

    def test_every_rower_metric_maps_to_a_role(self):
        for metric in ("speed", "stroke", "stroke_rate", "power", "split_500m"):
            self.assertEqual(ROLE_OF_METRIC[metric], "rower")

    def test_role_status_live_and_stale(self):
        r = RowRoles()
        devices = {(200, 1): {"devtype": 200, "devnum": 1, "last": 100.0}}
        live = role_status(r, devices, now=101.0)
        self.assertTrue(live["rower"]["live"])
        self.assertTrue(live["rower"]["required"])
        self.assertFalse(live["heart_rate"]["live"])
        stale = role_status(r, devices, now=110.0)
        self.assertFalse(stale["rower"]["live"])
        self.assertEqual(stale["rower"]["age_s"], 10.0)


if __name__ == "__main__":
    unittest.main()
