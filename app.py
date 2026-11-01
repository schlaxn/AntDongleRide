"""Local debug UI for the ANT USB stick: live packet log + sensor time graphs.

    python app.py            ->  http://127.0.0.1:5000

Binds to localhost only.
"""
import argparse
import asyncio
import csv
import io
import json
import queue
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import ble as ble_mod
from ble import BleCsc, BleHeartRate
from ant.profiles import DEVICE_TYPES
from ant.session import AntSession
from ant.usbdev import AntUsbError
from ride import RideEngine
from sensors import SensorRoles, role_status
from sim import SimulatedRider
from workout import DEFAULT_GRADIENTS, Workout, parse_shorthand

ROOT = Path(__file__).resolve().parent
RECORDINGS = ROOT / "recordings"
CONFIG_PATH = ROOT / "config.json"
DEFAULT_CONFIG = {"wheel_circumference_m": 2.105, "gradients": dict(DEFAULT_GRADIENTS)}

app = Flask(__name__, static_folder="static", static_url_path="/static")
session = AntSession()
roles = SensorRoles()
engine = RideEngine(session, RECORDINGS, roles)
simulator: SimulatedRider | None = None


def load_config() -> dict:
    try:
        return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


session.decoder.wheel_m = load_config()["wheel_circumference_m"]


def _json_error(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def state():
    return jsonify(session.status())


@app.post("/api/connect")
def connect():
    try:
        return jsonify({"ok": True, **session.connect()})
    except AntUsbError as exc:
        return _json_error(str(exc), 503)


@app.post("/api/disconnect")
def disconnect():
    session.disconnect()
    return jsonify({"ok": True})


@app.post("/api/reset")
def reset():
    if not session.connected:
        return _json_error("Not connected")
    session.channels.clear()
    session.reset()
    return jsonify({"ok": True})


@app.post("/api/scan")
def scan():
    if not session.connected:
        return _json_error("Not connected")
    body = request.get_json(silent=True) or {}
    types = [int(t) for t in body.get("types", list(DEVICE_TYPES))]
    types = [t for t in types if t in DEVICE_TYPES][:8]
    if not types:
        return _json_error("No valid device types")
    session.scan(types)
    return jsonify({"ok": True, "channels": session.status()["channels"]})


@app.post("/api/stop")
def stop():
    if session.connected:
        session.stop_scan()
    return jsonify({"ok": True})


@app.post("/api/send")
def send_raw():
    """Send an arbitrary ANT message: {"msg_id": 74, "data": "00"} (hex payload)."""
    if not session.connected:
        return _json_error("Not connected")
    body = request.get_json(silent=True) or {}
    try:
        msg_id = int(str(body["msg_id"]), 0)
        data = bytes.fromhex(body.get("data", "").replace(" ", ""))
    except (KeyError, ValueError) as exc:
        return _json_error(f"Bad message: {exc}")
    session.send(msg_id, data)
    return jsonify({"ok": True})


ble = BleHeartRate(on_bpm=session.ingest_ble_bpm, on_event=session.ble_event)
# Bluetooth speed and cadence sensor: its samples are "combined" (type 121) with device number -2
ble_csc = BleCsc(on_sample=lambda ts, metric, value, unit: session.ingest_sample(ts, 121, -2, metric, value, unit),
                 on_event=session.ble_event, wheel_m=lambda: session.decoder.wheel_m)


@app.post("/api/ble/scan")
def ble_scan():
    try:
        devices = asyncio.run(ble_mod.scan(6.0))
    except Exception as exc:
        return _json_error(f"Bluetooth scan failed: {exc}. Is Bluetooth switched on?", 503)
    return jsonify({"ok": True, "devices": devices})


@app.post("/api/ble/start")
def ble_start():
    body = request.get_json(silent=True) or {}
    address = body.get("address")
    if not address:
        return _json_error("No device address")
    ble.start(address)
    return jsonify({"ok": True, "state": ble.state})


@app.post("/api/ble/stop")
def ble_stop():
    ble.stop()
    return jsonify({"ok": True})


@app.post("/api/ble/csc/start")
def ble_csc_start():
    body = request.get_json(silent=True) or {}
    address = body.get("address")
    if not address:
        return _json_error("No device address")
    ble_csc.start(address)
    return jsonify({"ok": True, "state": ble_csc.state})


@app.post("/api/ble/csc/stop")
def ble_csc_stop():
    ble_csc.stop()
    return jsonify({"ok": True})


@app.get("/api/ble/state")
def ble_state():
    return jsonify({"running": ble.running, "address": ble.address, "state": ble.state})


@app.get("/ride")
def ride_page():
    return send_from_directory(app.static_folder, "ride.html")


@app.get("/history")
def history_page():
    return send_from_directory(app.static_folder, "history.html")


def _workout_from_body(body: dict) -> Workout:
    overrides = {}
    for key in ("warmup_s", "cooldown_s", "between_s"):
        if key in body:
            overrides[key] = int(body[key])
    if "skip_last_recover" in body:
        overrides["skip_last_recover"] = bool(body["skip_last_recover"])
    if "gradients" in body:
        overrides["gradients"] = {**DEFAULT_GRADIENTS, **{k: float(v) for k, v in body["gradients"].items()}}
    if "shorthand" in body:
        return parse_shorthand(str(body["shorthand"]), **overrides)
    fields = {k: int(body[k]) for k in ("blocks", "reps", "on_s", "off_s") if k in body}
    return Workout(**fields, **overrides)


@app.get("/api/workout")
def get_workout():
    if engine.state is None:
        return jsonify(parse_shorthand("3-13-30-15").to_dict() | {"loaded": False})
    return jsonify(engine.state.workout.to_dict() | {"loaded": True})


@app.post("/api/workout")
def set_workout():
    try:
        w = _workout_from_body(request.get_json(silent=True) or {})
        engine.load(w)
    except (ValueError, TypeError, RuntimeError) as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True, **w.to_dict()})


def _ride_action(fn):
    try:
        fn()
    except RuntimeError as exc:
        return _json_error(str(exc), 409)
    return jsonify({"ok": True, "status": engine.state.status if engine.state else "none"})


@app.post("/api/ride/start")
def ride_start():
    return _ride_action(engine.start)


@app.post("/api/ride/pause")
def ride_pause():
    return _ride_action(engine.pause)


@app.post("/api/ride/resume")
def ride_resume():
    return _ride_action(engine.resume)


@app.post("/api/ride/stop")
def ride_stop():
    return _ride_action(engine.stop)


@app.get("/api/ride/state")
def ride_state():
    if engine.state is None:
        return jsonify({"status": "none"})
    return jsonify(engine.state.snapshot(time.time()))


@app.get("/api/rides")
def list_rides():
    rides = []
    if RECORDINGS.exists():
        for d in sorted(RECORDINGS.iterdir(), reverse=True):
            doc_path = d / "session.json"
            if not doc_path.exists():
                continue
            try:
                doc = json.loads(doc_path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            rides.append({
                "id": d.name,
                "workout": doc.get("workout", {}).get("shorthand"),
                "summary": doc.get("summary", {}),
            })
    return jsonify(rides)


@app.get("/api/rides/<ride_id>/<name>")
def get_ride_file(ride_id: str, name: str):
    if name not in ("ride.csv", "samples.csv", "session.json"):
        return _json_error("Unknown file", 404)
    folder = RECORDINGS / ride_id
    if not folder.is_dir():
        return _json_error("No such ride", 404)
    return send_from_directory(folder, name, as_attachment=name.endswith(".csv"))


@app.get("/api/config")
def get_config():
    return jsonify(load_config())


@app.post("/api/config")
def set_config():
    body = request.get_json(silent=True) or {}
    cfg = load_config()
    try:
        if "wheel_circumference_m" in body:
            cfg["wheel_circumference_m"] = float(body["wheel_circumference_m"])
            if not 0.5 <= cfg["wheel_circumference_m"] <= 4.0:
                raise ValueError("Wheel circumference must be between 0.5 and 4.0 m")
        if "gradients" in body:
            cfg["gradients"] = {**DEFAULT_GRADIENTS, **{k: float(v) for k, v in body["gradients"].items()}}
    except (ValueError, TypeError) as exc:
        return _json_error(str(exc))
    save_config(cfg)
    session.decoder.wheel_m = cfg["wheel_circumference_m"]
    return jsonify({"ok": True, **cfg})


@app.get("/api/sensors")
def sensors_state():
    now = time.time()
    devices = []
    for d in session.devices.values():
        name = DEVICE_TYPES.get(d["devtype"], (f"type {d['devtype']}",))[0]
        if d["devnum"] == -1:
            name = "Bluetooth watch"
        elif d["devnum"] == -2:
            name = "Bluetooth speed & cadence"
        devices.append({**d, "name": name, "age_s": round(now - d.get("last", now), 1)})
    devices.sort(key=lambda d: (d["devtype"], d["devnum"]))
    return jsonify({
        "connected": session.connected,
        "devices": devices,
        "roles": role_status(roles, list(session.devices.values()), now),
        "channels": {str(ch): {"devtype": dt, "devnum": session.channel_devnum.get(ch, 0)}
                     for ch, dt in session.channels.items()},
        "ble": {"running": ble.running, "address": ble.address, "state": ble.state},
        "ble_csc": {"running": ble_csc.running, "address": ble_csc.address, "state": ble_csc.state,
                    "has_crank": ble_csc.has_crank},
    })


@app.post("/api/sensors/search")
def sensors_search():
    if not session.connected:
        return _json_error("Stick not connected")
    body = request.get_json(silent=True) or {}
    try:
        devtype = int(body.get("devtype", 0))
        if devtype not in (120, 121, 122, 123):
            raise ValueError("Unknown device type")
        session.search(devtype)
    except (ValueError, RuntimeError) as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True})


@app.post("/api/sensors/select")
def sensors_select():
    body = request.get_json(silent=True) or {}
    try:
        role = str(body["role"])
        devtype, devnum = int(body["devtype"]), int(body["devnum"])
        roles.select(role, devtype, devnum)
        if devnum > 0 and session.connected:
            # listen only to this sensor, and stop the wildcard search for its type
            session.pair(devtype, devnum)
            session.close_search(devtype)
    except (KeyError, ValueError, TypeError, RuntimeError) as exc:
        return _json_error(str(exc))
    return jsonify({"ok": True})


@app.post("/api/sensors/reset")
def sensors_reset():
    if session.connected:
        session.stop_scan()
    roles.selection = SensorRoles().selection
    return jsonify({"ok": True})


@app.get("/api/test/speed-multiplier")
def get_speed_multiplier():
    return jsonify({"factor": engine.speed_multiplier})


@app.post("/api/test/speed-multiplier")
def set_speed_multiplier():
    """Testing only: multiply the speed the ride uses (e.g. x10 while the speed sensor is faked)."""
    body = request.get_json(silent=True) or {}
    try:
        factor = float(body.get("factor", 1))
    except (TypeError, ValueError):
        return _json_error("factor must be a number")
    if not 0 < factor <= 100:
        return _json_error("factor must be between 0 and 100")
    engine.speed_multiplier = factor
    return jsonify({"ok": True, "factor": factor})


@app.get("/api/export.csv")
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp_iso", "unix_ts", "device", "devtype", "devnum", "metric", "value", "unit"])
    for s in list(session.samples):
        name = DEVICE_TYPES.get(s.devtype, (f"type{s.devtype}",))[0]
        iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(s.ts)) + f".{int((s.ts % 1) * 1000):03d}"
        w.writerow([iso, f"{s.ts:.3f}", name, s.devtype, s.devnum, s.metric, s.value, s.unit])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=ant_samples.csv"},
    )


@app.get("/api/stream")
def stream():
    """Server-Sent Events: every frame, decoded sample, event and error."""
    q = session.subscribe()

    def gen():
        try:
            yield "retry: 1000\n\n"
            while True:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            session.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ANT+ debug UI and virtual trainer")
    ap.add_argument("--simulate", action="store_true",
                    help="inject a simulated rider (no sensors needed) for testing the ride page")
    ap.add_argument("--no-cadence", action="store_true",
                    help="with --simulate: send speed and heart rate only (no cadence sensor)")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    if args.simulate:
        simulator = SimulatedRider(session, engine, cadence=not args.no_cadence)
        simulator.start()
        print("Simulated rider running: " + ("heart rate, cadence and speed" if not args.no_cadence
                                             else "heart rate and speed (no cadence)") + " follow the workout phases.")
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
