"""Rowing app: the rower twin of the ride app.

    python row_app.py --simulate              -> http://127.0.0.1:5001/row, no hardware
    python row_app.py --serial COM6           -> the real S4 on COM6
    python row_app.py --serial COM6 --simulate (simulate is ignored when a serial port is given)

Binds to localhost only. The ride app (app.py, port 5000) is untouched.
"""
import argparse
from pathlib import Path

from flask import Flask

from rower.api import make_blueprint
from rower.engine import RowEngine
from rower.hub import RowHub
from rower.profiles import Decoder
from rower.roles import RowRoles
from rower.serialdev import RowerSerial
from rower.sim import SimulatedRower
from rower.source import RowerSource
from rower.web import bp as rower_debug_bp

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
RECORDINGS = ROOT / "recordings" / "rows"

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
hub = RowHub()
roles = RowRoles()
engine = RowEngine(hub, RECORDINGS, roles)
serial = RowerSerial()
source = RowerSource(serial, hub, Decoder())
simulator = SimulatedRower(hub, engine)   # started from the sensor dialog or --simulate

app.register_blueprint(make_blueprint(engine, hub, source, roles, STATIC, simulator))
app.register_blueprint(rower_debug_bp)  # raw serial debug page: /rower-debug


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rowing app (WaterRower S4)")
    ap.add_argument("--simulate", action="store_true", help="drive the app with a simulated rower")
    ap.add_argument("--serial", metavar="PORT", help="read the S4 on this serial port, e.g. COM6")
    ap.add_argument("--port", type=int, default=5001)
    args = ap.parse_args()
    if args.serial:
        source.connect(args.serial)
        print(f"Reading the S4 on {args.serial}.")
    elif args.simulate:
        simulator.start()
        print("Simulated rower running.")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
