"""HTTP routes for the rowing app (blueprint built from the engine, hub, source and roles)."""
import asyncio
import json
import queue
import time

from flask import Blueprint, Response, jsonify, request, send_from_directory

from workout import Workout

from .engine import DEFAULT_TARGETS
from .roles import role_status
from .serialdev import DEFAULT_BAUD, RowerError, list_ports


def make_blueprint(engine, hub, source, roles, static_dir, simulator=None, cal=None) -> Blueprint:
    bp = Blueprint("row", __name__)
    ble_state = {"hr": None}

    def cal_call(method: str, *args):
        if cal is None:
            return err("Calibration is not available in this build", 503)
        try:
            return jsonify({"ok": True, **getattr(cal, method)(*args)})
        except (RuntimeError, ValueError) as exc:
            return err(str(exc), 409)

    @bp.get("/row-cal")
    def cal_page():
        return send_from_directory(static_dir, "row_cal.html")

    @bp.get("/api/row/cal/state")
    def cal_state():
        if cal is None:
            return err("Calibration is not available in this build", 503)
        return jsonify(cal.state())

    @bp.post("/api/row/cal/start")
    def cal_start():
        body = request.get_json(silent=True) or {}
        try:
            fill = int(body.get("fill_l", 17))
        except (TypeError, ValueError):
            return err("fill_l must be a whole number of litres")
        return cal_call("start", fill)

    @bp.post("/api/row/cal/retry")
    def cal_retry():
        return cal_call("retry")

    @bp.post("/api/row/cal/skip")
    def cal_skip():
        return cal_call("skip")

    @bp.post("/api/row/cal/redo-previous")
    def cal_redo():
        return cal_call("redo_previous")

    @bp.post("/api/row/cal/abort")
    def cal_abort():
        return cal_call("abort")

    @bp.post("/api/row/cal/reading")
    def cal_reading():
        body = request.get_json(silent=True) or {}
        try:
            speed = float(body.get("speed_ms"))
        except (TypeError, ValueError):
            return err("speed_ms must be a number (the monitor's m/s)")
        return cal_call("reading", speed)

    @bp.get("/api/row/sim")
    def sim_status():
        running = bool(simulator and simulator.running)
        return jsonify({"ok": True, "available": simulator is not None, "running": running})

    @bp.post("/api/row/sim/start")
    def sim_start():
        if simulator is None:
            return err("No simulator in this build", 503)
        simulator.start()
        return jsonify({"ok": True, "running": True})

    @bp.post("/api/row/sim/stop")
    def sim_stop():
        if simulator is not None:
            simulator.stop()
        return jsonify({"ok": True, "running": False})

    def err(msg: str, code: int = 400):
        return jsonify({"ok": False, "error": msg}), code

    @bp.get("/row")
    def page():
        return send_from_directory(static_dir, "row.html")

    @bp.get("/api/row/state")
    def state():
        st = engine.state
        if st is None:
            return jsonify({"status": "none"})
        return jsonify(st.snapshot(time.time()))

    def _control(fn):
        try:
            fn()
        except RuntimeError as exc:
            return err(str(exc), 409)
        return jsonify({"ok": True, "status": engine.state.status})

    @bp.post("/api/row/start")
    def start():
        return _control(engine.start)

    @bp.post("/api/row/pause")
    def pause():
        return _control(engine.pause)

    @bp.post("/api/row/resume")
    def resume():
        return _control(engine.resume)

    @bp.post("/api/row/stop")
    def stop():
        return _control(engine.stop)

    @bp.get("/api/row/workout")
    def get_workout():
        st = engine.state
        return jsonify({"ok": True, "loaded": True, "targets": st.targets, **st.workout.to_dict()})

    @bp.post("/api/row/workout")
    def set_workout():
        """Same parameters as the ride app's /api/workout, with watt targets per interval kind
        in place of gradients: blocks, reps, on_s, off_s, warmup_s, cooldown_s, between_s,
        skip_last_recover, targets {warmup, work, recover, between, cooldown}."""
        body = request.get_json(silent=True) or {}
        try:
            wk = Workout(
                warmup_s=int(body.get("warmup_s", 300)),
                cooldown_s=int(body.get("cooldown_s", 300)),
                blocks=int(body.get("blocks", 2)),
                reps=int(body.get("reps", 5)),
                on_s=int(body.get("on_s", 60)),
                off_s=int(body.get("off_s", 60)),
                between_s=int(body.get("between_s", 120)),
                skip_last_recover=bool(body.get("skip_last_recover", True)),
            )
            targets = {**DEFAULT_TARGETS, **{k: int(v) for k, v in (body.get("targets") or {}).items()
                                               if k in DEFAULT_TARGETS}}
            if any(not (0 <= v <= 180) for v in targets.values()):
                return err("Targets must be between 0 and 180 W")
        except (ValueError, TypeError) as exc:
            return err(str(exc))
        try:
            engine.load(wk, targets)
        except RuntimeError as exc:
            return err(str(exc), 409)
        return jsonify({"ok": True, "loaded": True, "targets": targets, **wk.to_dict()})

    @bp.post("/api/row/bots")
    def set_bots():
        body = request.get_json(silent=True) or {}
        engine.set_bots(bool(body.get("on", True)))
        return jsonify({"ok": True, "on": engine.state.bots_on})

    @bp.get("/api/row/stream")
    def stream():
        q = hub.subscribe()

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
                hub.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @bp.get("/api/row/ports")
    def ports():
        try:
            return jsonify({"ok": True, "ports": list_ports(), "default_baud": DEFAULT_BAUD})
        except ImportError:
            return err("pyserial is not installed", 503)

    @bp.post("/api/row/connect")
    def connect():
        body = request.get_json(silent=True) or {}
        port = str(body.get("port", "")).strip()
        if not port:
            return err("No port selected")
        try:
            baud = int(body.get("baud", DEFAULT_BAUD))
            info = source.connect(port, baud)
        except (RowerError, ValueError) as exc:
            return err(str(exc), 409)
        except Exception as exc:
            return err(f"Could not open {port}: {exc}", 503)
        return jsonify({"ok": True, **info})

    @bp.post("/api/row/disconnect")
    def disconnect():
        source.disconnect()
        return jsonify({"ok": True})

    @bp.get("/api/row/sensors")
    def sensors():
        now = time.time()
        return jsonify({
            "ok": True,
            "connected": source.connected,
            "roles": role_status(roles, hub.devices, now),
            "ble": {"hr_address": ble_state["hr"]},
        })

    @bp.post("/api/row/sensors/select")
    def select():
        body = request.get_json(silent=True) or {}
        try:
            roles.select(str(body["role"]), int(body["devtype"]), int(body.get("devnum", 0)))
        except (KeyError, ValueError, TypeError) as exc:
            return err(str(exc))
        return jsonify({"ok": True})

    @bp.post("/api/row/ble/scan")
    def ble_scan():
        try:
            from ble import scan
            devices = asyncio.run(scan(6.0))
        except Exception as exc:
            return err(f"Bluetooth scan failed: {exc}", 503)
        return jsonify({"ok": True, "devices": devices})

    @bp.post("/api/row/ble/start")
    def ble_start():
        body = request.get_json(silent=True) or {}
        address = str(body.get("address", "")).strip()
        if not address:
            return err("No address given")
        try:
            from ble import BleHeartRate
        except Exception as exc:
            return err(f"Bluetooth unavailable: {exc}", 503)
        if ble_state.get("obj") is None:
            ble_state["obj"] = BleHeartRate(
                on_bpm=lambda ts, bpm, raw: hub.ingest_ble_bpm(ts, bpm),
                on_event=lambda text: hub.event(text))
        ble_state["obj"].start(address)
        ble_state["hr"] = address
        return jsonify({"ok": True, "address": address})

    @bp.post("/api/row/ble/stop")
    def ble_stop():
        obj = ble_state.get("obj")
        if obj is not None:
            obj.stop()
        ble_state["hr"] = None
        return jsonify({"ok": True})

    return bp
