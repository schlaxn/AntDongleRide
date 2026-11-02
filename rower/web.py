"""Flask routes for the rower debug page (/rower-debug). Registered in app.py."""
import csv
import io
import json
import queue
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_from_directory

from .serialdev import DEFAULT_BAUD, RowerError, RowerSerial, list_ports, parse_payload

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
RECORDINGS = ROOT / "recordings"

bp = Blueprint("rower", __name__)
rower = RowerSerial()


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _csv(items) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["unix_ts", "dir", "len", "raw_hex", "text", "lines"])
    for it in items:
        w.writerow([f"{it['ts']:.3f}", it["dir"], it.get("len", 0), it.get("raw_hex", ""),
                    it.get("text", ""), " | ".join(it.get("lines", []))])
    return buf.getvalue()


@bp.get("/rower-debug")
def page():
    return send_from_directory(STATIC, "rower_debug.html")


@bp.get("/api/rower/ports")
def ports():
    try:
        return jsonify({"ok": True, "ports": list_ports(), "default_baud": DEFAULT_BAUD})
    except ImportError:
        return _err("pyserial is not installed (pip install -r requirements.txt)", 503)


@bp.get("/api/rower/status")
def status():
    return jsonify({"ok": True, **rower.info()})


@bp.post("/api/rower/connect")
def connect():
    body = request.get_json(silent=True) or {}
    port = str(body.get("port", "")).strip()
    if not port:
        return _err("No port selected")
    try:
        baud = int(body.get("baud", DEFAULT_BAUD))
    except (TypeError, ValueError):
        return _err("Baud must be a number")
    try:
        return jsonify({"ok": True, **rower.connect(port, baud)})
    except RowerError as exc:
        return _err(str(exc))
    except Exception as exc:  # OSError from the port, or ImportError from pyserial
        return _err(f"Could not open {port}: {exc}", 503)


@bp.post("/api/rower/disconnect")
def disconnect():
    rower.disconnect()
    return jsonify({"ok": True, **rower.info()})


@bp.post("/api/rower/send")
def send():
    body = request.get_json(silent=True) or {}
    try:
        data = parse_payload(str(body.get("text", "")), str(body.get("mode", "ascii")))
    except ValueError as exc:
        return _err(str(exc))
    if not data:
        return _err("Nothing to send")
    try:
        rower.send(data)
    except RowerError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"Send failed: {exc}", 503)
    return jsonify({"ok": True, "sent": len(data)})


@bp.post("/api/rower/clear")
def clear():
    rower.events.clear()
    return jsonify({"ok": True})


@bp.post("/api/rower/save")
def save():
    """Write the captured traffic to recordings/rower-<ts>/trace.log and trace.csv."""
    if not rower.events:
        return _err("Nothing captured yet")
    folder = RECORDINGS / time.strftime("rower-%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    items = list(rower.events)
    with (folder / "trace.log").open("w", encoding="utf-8") as fh:
        for it in items:
            stamp = time.strftime("%H:%M:%S", time.localtime(it["ts"])) + f".{int((it['ts'] % 1) * 1000):03d}"
            fh.write(f"{stamp} {it['dir']:3} [{it.get('len', 0):3}] {it.get('raw_hex', '')}  {it.get('text', '')}\n")
    (folder / "trace.csv").write_text(_csv(items), encoding="utf-8")
    return jsonify({"ok": True, "folder": str(folder), "items": len(items)})


@bp.get("/api/rower/export.csv")
def export_csv():
    return Response(_csv(list(rower.events)), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=rower_trace.csv"})


@bp.get("/api/rower/stream")
def stream():
    """Server-Sent Events: every RX/TX chunk and connection event."""
    q = rower.subscribe()

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
            rower.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
