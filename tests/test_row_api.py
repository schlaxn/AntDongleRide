import tempfile
import unittest
from pathlib import Path

from flask import Flask

from rower.api import make_blueprint
from rower.engine import RowEngine
from rower.hub import RowHub
from rower.profiles import Decoder
from rower.roles import RowRoles
from rower.serialdev import RowerSerial
from rower.source import RowerSource

STATIC = Path(__file__).resolve().parent.parent / "static"


class RowApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = RowHub()
        self.roles = RowRoles()
        self.engine = RowEngine(self.hub, Path(self.tmp.name), self.roles, tick_hz=20.0)
        self.source = RowerSource(RowerSerial(opener=lambda p, b: None), self.hub, Decoder())
        app = Flask(__name__)
        app.register_blueprint(make_blueprint(self.engine, self.hub, self.source,
                                              self.roles, STATIC))
        self.client = app.test_client()

    def tearDown(self):
        self.engine.shutdown()
        self.tmp.cleanup()

    def test_fill_setting_scales_the_power_from_now_on(self):
        self.assertEqual(self.client.get("/api/row/fill").get_json()["litres"], 17)
        r = self.client.post("/api/row/fill", json={"litres": 18.5}).get_json()
        self.assertEqual(r["litres"], 18.5)
        self.assertAlmostEqual(r["factor"], 1.081, places=3)
        self.assertAlmostEqual(self.source.decoder.fill_litres, 18.5)
        r = self.client.post("/api/row/fill", json={"litres": 25})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/row/fill", json={"litres": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertAlmostEqual(self.source.decoder.fill_litres, 18.5)

    def test_state_before_and_after_start(self):
        r = self.client.get("/api/row/state")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "preview")
        self.assertEqual(self.client.post("/api/row/start").get_json()["status"], "running")
        self.assertEqual(self.client.get("/api/row/state").get_json()["status"], "running")

    def test_pause_when_not_running_is_a_conflict(self):
        r = self.client.post("/api/row/pause")
        self.assertEqual(r.status_code, 409)
        self.assertFalse(r.get_json()["ok"])

    def test_workout_round_trip_with_targets(self):
        body = {"blocks": 1, "reps": 2, "on_s": 20, "off_s": 10, "warmup_s": 0,
                "cooldown_s": 0, "between_s": 0, "skip_last_recover": True,
                "targets": {"work": 160, "recover": 60}}
        r = self.client.post("/api/row/workout", json=body)
        self.assertEqual(r.status_code, 200)
        got = r.get_json()
        self.assertEqual(got["targets"]["work"], 160)
        self.assertEqual(got["targets"]["recover"], 60)
        self.assertEqual(got["blocks"], 1)
        again = self.client.get("/api/row/workout").get_json()
        self.assertEqual(again["targets"]["work"], 160)
        self.assertTrue(again["loaded"])

    def test_bad_workout_is_rejected(self):
        self.assertEqual(self.client.post("/api/row/workout", json={"blocks": 0}).status_code, 400)
        self.assertEqual(self.client.post("/api/row/workout",
                                          json={"targets": {"work": 200}}).status_code, 400)

    def test_bots_toggle(self):
        r = self.client.post("/api/row/bots", json={"on": False})
        self.assertFalse(r.get_json()["on"])
        r = self.client.post("/api/row/bots", json={"on": True})
        self.assertTrue(r.get_json()["on"])

    def test_sensor_select_validates(self):
        ok = self.client.post("/api/row/sensors/select",
                              json={"role": "heart_rate", "devtype": 120, "devnum": -1})
        self.assertEqual(ok.status_code, 200)
        bad = self.client.post("/api/row/sensors/select",
                               json={"role": "rower", "devtype": 120, "devnum": 0})
        self.assertEqual(bad.status_code, 400)

    def test_sensors_status_shape(self):
        r = self.client.get("/api/row/sensors").get_json()
        self.assertIn("rower", r["roles"])
        self.assertFalse(r["roles"]["rower"]["live"])
        self.assertFalse(r["connected"])

    def test_page_is_served(self):
        # the page file arrives in the browser phase; a missing file is a clean 404, not a crash
        r = self.client.get("/row")
        self.assertIn(r.status_code, (200, 404))


if __name__ == "__main__":
    unittest.main()
