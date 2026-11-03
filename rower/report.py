"""Static HTML report for one rowing session folder (recordings/rows/<stamp>/).

    python -m rower.report recordings/rows/2026-11-03_144329

Reads row.csv (display values per second), speed_1s.csv (raw speed per second) and
strokes.csv (SS and SE markers). Writes report.html into the same folder. No server needed.
"""
import csv
import json
import sys
from pathlib import Path


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _series(rows, key, t_key="elapsed_s"):
    xs, ys = [], []
    for r in rows:
        x, y = _num(r.get(t_key)), _num(r.get(key))
        if x is not None and y is not None:
            xs.append(round(x, 1))
            ys.append(round(y, 2))
    return xs, ys


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _session(folder: Path) -> dict:
    p = folder / "session.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def plan_stats(session: dict, rows: list[dict]) -> list[dict]:
    """Per interval of the recorded workout: the target against the actual means of that interval."""
    targets = session.get("targets") or {}
    out = []
    for g in (session.get("workout") or {}).get("segments") or []:
        window = [r for r in rows if g["start"] <= (_num(r.get("elapsed_s")) or -1) < g["end"]]
        pw = [_num(r.get("power_w")) for r in window if _num(r.get("power_w")) is not None]
        sp = [_num(r.get("stroke_rate_spm")) for r in window if _num(r.get("stroke_rate_spm")) is not None]
        hr = [_num(r.get("hr_bpm")) for r in window if _num(r.get("hr_bpm")) is not None]
        out.append({"kind": g["kind"], "start": g["start"], "end": g["end"], "rep": g.get("rep", 0),
                    "target_w": targets.get(g["kind"]),
                    "mean_power_w": round(_mean(pw), 1) if pw else None,
                    "mean_spm": round(_mean(sp), 1) if sp else None,
                    "mean_hr": round(_mean(hr)) if hr else None})
    return out


def replay(folder: Path, session: dict | None = None) -> list[dict]:
    """Per-second display values recomputed from samples.csv with the current engine logic
    (the 15 s averages and the change detection), as the page would have shown them."""
    from workout import parse_shorthand
    from rower.engine import DEFAULT_SHORTHAND, METRICS, RowState
    session = session or {}
    samples = _read(folder / "samples.csv")
    wk = session.get("workout") or {}
    workout = parse_shorthand(wk.get("shorthand", DEFAULT_SHORTHAND), warmup_s=wk.get("warmup_s", 300),
                              cooldown_s=wk.get("cooldown_s", 300), between_s=wk.get("between_s", 120))
    st = RowState(workout, targets=session.get("targets"))
    # the decoder's distance runs from its first stroke, not from Start: take the value at Start
    first_dist = next((_num(x.get("value")) for x in samples if x.get("metric") == "distance"), None)
    if first_dist is not None:
        st.update("distance", 0.0, first_dist)
    st.start()
    rows, next_sec, last = [], None, None
    for s in samples:
        ts, val, metric = _num(s.get("ts")), _num(s.get("value")), s.get("metric")
        if ts is None or val is None:
            continue
        if next_sec is None:
            next_sec, last = ts + 1.0, ts
        while ts >= next_sec:
            st.advance(next_sec, next_sec - last)
            last = next_sec
            snap = st.snapshot(next_sec)
            rows.append({k: snap.get(k) for k in ("elapsed_s", "speed_ms", "split_s", "power_w",
                                                  "stroke_rate_spm", "strokes", "distance_m",
                                                  "hr_bpm", "target_w", "phase")})
            next_sec += 1.0
        if metric in METRICS:
            st.update(metric, ts, val)
    return rows


BANDS = (0.05, 0.08, 0.12)
CONFIRMS = (1.0, 2.0, 3.0)


def _filters(speeds: list[float], spm_pts: list[tuple[float, float]]) -> dict:
    """The adaptive filter for every band and confirm time: on the raw 1 s speed, on the power the cube
    law gives from that speed (1.78 * v^3, 17 L), and on the stroke rate (one value per stroke)."""
    from rower.smoothing import AdaptiveSmoother, smooth_series
    power = [1.78 * v ** 3 for v in speeds]
    out = {}
    for b in BANDS:
        for c in CONFIRMS:
            key = f"band {int(b * 100)} %, confirm {int(c)} s"
            sm = AdaptiveSmoother(band=b, confirm_s=c)
            spm, last = [], None
            for t, rate in spm_pts:
                spm.append(round(sm.update(rate, None if last is None else t - last), 2))
                last = t
            out[key] = {"speed": [round(x, 3) for x in smooth_series(speeds, band=b, confirm_s=c)],
                        "power": [round(x, 2) for x in smooth_series(power, band=b, confirm_s=c)],
                        "spm": spm}
    return out


def _redecode_power(samples: list[dict]) -> list[tuple[float, float]]:
    """Power once per stroke, recomputed from the recorded raw data with the current decoder:
    the codes and the stroke markers are fed back in as the S4 lines they were."""
    from rower.profiles import Decoder
    d = Decoder()
    out = []
    for x in samples:
        ts, v, m = _num(x.get("ts")), _num(x.get("value")), x.get("metric")
        if ts is None or v is None:
            continue
        if m == "code":
            line = f"P{int(v):02X}"
        elif m == "stroke":
            line = "SS"
        elif m == "stroke_end":
            line = "SE"
        else:
            continue
        out += [(smp.ts, smp.value) for smp in d.decode_line(line, ts) if smp.metric == "power"]
    return out


def summarise(folder: Path, rows: list[dict] | None = None, session: dict | None = None) -> dict:
    # stroke rate: one value per stroke, from the SS markers (60 / interval)
    ss = [_num(x["ts"]) for x in _read(folder / "strokes.csv") if x.get("event") == "SS" and _num(x.get("ts")) is not None]
    spm_pts = [(b_, 60.0 / (b_ - a_)) for a_, b_ in zip(ss, ss[1:]) if 0 < b_ - a_ < 10]
    # the time base: the session's start (the Start press), so every series lines up with the workout
    t0 = next((e["ts"] for e in (session or {}).get("events", []) if e.get("event") == "start"), None)
    samples = _read(folder / "samples.csv")
    if t0 is None:
        t0 = min((_num(x["ts"]) for x in samples if _num(x.get("ts")) is not None), default=0.0)
    raw_power = _redecode_power(samples)
    row = rows if rows is not None else _read(folder / "row.csv")
    speed = _read(folder / "speed_1s.csv")
    strokes = _read(folder / "strokes.csv")
    dur = max((_num(r["elapsed_s"]) or 0 for r in row), default=0.0)
    dist = _num(row[-1]["distance_m"]) if row else None
    power = [_num(r["power_w"]) for r in row]
    spm = [_num(r["stroke_rate_spm"]) for r in row]
    hr = [_num(r["hr_bpm"]) for r in row]
    # stroke timing from the sensor markers: drive = SS to SE, recovery = SE to the next SS
    drives, recs = [], []
    last_ss = last_se = None
    for s in strokes:
        t = _num(s["ts"])
        if s["event"] == "SS":
            if last_se is not None and last_ss is not None and last_se > last_ss:
                recs.append(t - last_se)
            last_ss = t
        elif s["event"] == "SE" and last_ss is not None:
            drives.append(t - last_ss)
            last_se = t
    drive_m, rec_m = _mean(drives), _mean(recs)
    cycle = (drive_m + rec_m) if drive_m is not None and rec_m is not None else None
    moving = [_num(s["speed_ms"]) for s in speed if _num(s["speed_ms"]) is not None and _num(s["speed_ms"]) > 0.5]
    return {
        "name": folder.name,
        "duration_s": round(dur, 1),
        "distance_m": None if dist is None else round(dist),
        "strokes": int(float(row[-1]["strokes"])) if row and row[-1].get("strokes") else len(drives),
        "avg_power_w": round(_mean(power), 1) if _mean(power) is not None else None,
        "max_power_w": round(max(p for p in power if p is not None), 1) if any(p is not None for p in power) else None,
        "avg_spm": round(_mean(spm), 1) if _mean(spm) is not None else None,
        "avg_hr": round(_mean(hr)) if _mean(hr) is not None else None,
        "pace_s": round(500.0 * dur / dist, 1) if dist and dist > 0 else None,
        "avg_speed_moving": round(_mean(moving), 2) if moving else None,
        "drive_s": round(drive_m, 2) if drive_m is not None else None,
        "recovery_s": round(rec_m, 2) if rec_m is not None else None,
        "drive_share_pct": round(100 * drive_m / cycle, 1) if cycle else None,
        "series": {
            # raw: power once per stroke (interpolated velocity), as the monitor updates it
            "power": ([round(t - t0, 2) for t, _ in raw_power], [round(v, 2) for _, v in raw_power]),
            # raw: stroke rate, one value per stroke (for the postprocessing comparison)
            "spm_raw": ([round(t - t0, 2) for t, _ in spm_pts], [round(r, 2) for _, r in spm_pts]),
            # raw: the cube law on the raw 1 s speed, for the postprocessing comparison only
            "power1s": ([round(_num(s["t_s"]) - t0, 1) for s in speed if _num(s["speed_ms"]) is not None],
                        [round(1.78 * _num(s["speed_ms"]) ** 3, 2) for s in speed if _num(s["speed_ms"]) is not None]),
            "target": _series(row, "target_w"),
            "spm": _series(row, "stroke_rate_spm"),
            "hr": _series(row, "hr_bpm"),
            "distance": _series(row, "distance_m"),
            "split": ([x for x, y in zip(*_series(row, "split_s")) if y <= 300],
                      [y for y in _series(row, "split_s")[1] if y <= 300]),   # start-up junk dropped
            # raw: the speed averaged over each whole second, no smoothing
            "speed1s": ([round(_num(s["t_s"]) - t0, 1) for s in speed if _num(s["speed_ms"]) is not None],
                        [_num(s["speed_ms"]) for s in speed if _num(s["speed_ms"]) is not None]) if speed else ([], []),
        },
        "plan": plan_stats(session or {}, row),
        "filters": _filters([_num(x["speed_ms"]) for x in speed if _num(x["speed_ms"]) is not None], spm_pts),
        "targets": (session or {}).get("targets") or {},
        "segments": [{"kind": g["kind"], "start": g["start"], "end": g["end"],
                      "target_w": ((session or {}).get("targets") or {}).get(g["kind"])}
                     for g in ((session or {}).get("workout") or {}).get("segments") or []],
        "total_s": ((session or {}).get("workout") or {}).get("total_s"),
        "drives": [round(d, 2) for d in drives],
        "recoveries": [round(r, 2) for r in recs],
    }


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Row report __NAME__</title>
<style>
:root{--bg:#f6f7f9;--fg:#1d2330;--mut:#6b7280;--grid:#e3e6eb;--card:#ffffff}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#14171c;--fg:#e6e9ef;--mut:#8b93a1;--grid:#2a2f38;--card:#1c2026}}
:root[data-theme="dark"]{--bg:#14171c;--fg:#e6e9ef;--mut:#8b93a1;--grid:#2a2f38;--card:#1c2026}
body{margin:0;font:14px system-ui,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg)}
header{padding:16px 20px 4px}
h1{font-size:20px;margin:0 0 4px}
.hint{font-size:12px;color:var(--mut)}
.sum{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px;padding:8px 20px}
.kpi{background:var(--card);border-radius:8px;padding:8px 10px}
.kpi b{display:block;font-size:17px;margin-top:2px}
.kpi span{color:var(--mut);font-size:12px}
.chart{margin:8px 20px;background:var(--card);border-radius:8px;padding:6px 8px}
.chart .title{font-weight:600;font-size:13px;padding:2px 4px}
canvas{display:block;width:100%;height:190px}
.legend{font-size:12px;color:var(--mut);padding:0 4px 4px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}
</style></head><body>
<header><h1>Row report __NAME__</h1>
<div class="hint">Recomputed from the recorded samples with the current display logic: power and stroke rate are 15 s averages with fast change detection; pace is the 3-stroke average. Speed is raw, averaged per second. Distance is the ticks, whole metres.</div></header>
<div class="sum" id="kpis"></div>
<div class="chart"><div class="title">Intervals: target against what was rowed</div><div id="plan_table" style="padding:4px"></div></div>
<div class="chart"><div class="title">Power (W): once per stroke, from the interpolated velocity, and target</div><canvas id="c_power"></canvas><div class="legend" id="l_power"></div></div>
<div class="chart"><div class="title">Speed (m/s): raw, averaged per second</div><canvas id="c_speed"></canvas></div>
<div class="chart"><div class="title">Distance (m) and split (s per 500 m)</div><canvas id="c_dist"></canvas><div class="legend" id="l_dist"></div></div>
<div class="chart"><div class="title">Stroke rate (spm) and heart rate (bpm)</div><canvas id="c_spm"></canvas><div class="legend" id="l_spm"></div></div>
<div class="chart"><div class="title">Drive and recovery per stroke (s)</div><canvas id="c_strokes"></canvas><div class="legend" id="l_strokes"></div></div>
<div class="chart"><div class="title">Postprocessing: raw against the adaptive filter
  <select id="fsel" style="margin-left:12px;font:inherit"></select></div>
  <div class="legend">Speed (m/s): raw per second, and filtered</div><canvas id="c_fspeed"></canvas><div class="legend" id="l_fspeed"></div>
  <div class="legend">Power (W): raw per second from the cube law, and filtered</div><canvas id="c_fpower"></canvas><div class="legend" id="l_fpower"></div>
  <div class="legend">Stroke rate (spm): raw per stroke, and filtered</div><canvas id="c_fspm"></canvas><div class="legend" id="l_fspm"></div></div>
<script>
const R = __DATA__;
const COL = {a:"#2563eb", b:"#dc2626", c:"#16a34a", d:"#9333ea", e:"#ea580c"};
const KIND = {warmup:"#94a3b8", work:"#f87171", recover:"#4ade80", between:"#fbbf24", cooldown:"#60a5fa"};
const css = getComputedStyle(document.documentElement);
const grid = () => css.getPropertyValue("--grid").trim() || "#e3e6eb";
const mut = () => css.getPropertyValue("--mut").trim() || "#6b7280";

function kpi(label, v, unit) {
  const s = v == null ? "–" : v + (unit ? " " + unit : "");
  return `<div class="kpi"><span>${label}</span><b>${s}</b></div>`;
}
function fmtPace(s) { if (s == null) return null; const m = Math.floor(s / 60), r = (s - m * 60).toFixed(0).padStart(2, "0"); return `${m}:${r} /500 m`; }
function fmtDur(s) { const m = Math.floor(s / 60), r = Math.round(s - m * 60); return `${m}:${String(r).padStart(2, "0")}`; }

document.getElementById("kpis").innerHTML = [
  kpi("Duration", fmtDur(R.duration_s), "min:s"),
  kpi("Distance", R.distance_m, "m"),
  kpi("Strokes", R.strokes),
  kpi("Avg pace", fmtPace(R.pace_s)),
  kpi("Avg power", R.avg_power_w, "W"),
  kpi("Max power", R.max_power_w, "W"),
  kpi("Avg stroke rate", R.avg_spm, "spm"),
  kpi("Avg heart rate", R.avg_hr, "bpm"),
  kpi("Drive", R.drive_s, "s"),
  kpi("Recovery", R.recovery_s, "s"),
  kpi("Drive share", R.drive_share_pct, "%"),
].join("");

// one plot: series = [{xs, ys, color, label, step}], y axis from zero when all values are positive
function plot(id, series, legendId, opts = {}) {
  const c = document.getElementById(id);
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const live = series.filter(s => s.xs && s.xs.length);
  if (!live.length) { g.fillStyle = mut(); g.fillText("no data", 10, 20); return; }
  const xs = live.flatMap(s => s.xs), ys = live.flatMap(s => s.ys);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = opts.zero ? 0 : Math.min(...ys), y1 = Math.max(...ys);
  if (y1 === y0) y1 = y0 + 1;
  const pad = 36;
  const X = t => pad + (t - x0) / (x1 - x0 || 1) * (w - pad - 8);
  const Y = v => h - 22 - (v - y0) / (y1 - y0) * (h - 30);
  // the workout's intervals as bands behind the data (time axis in seconds from the start)
  for (const s of (R.segments || [])) {
    g.globalAlpha = 0.14; g.fillStyle = KIND[s.kind] || "#999";
    g.fillRect(X(s.start), 0, Math.max(1, X(s.end) - X(s.start)), h - 20);
    g.globalAlpha = 1;
  }
  g.strokeStyle = grid(); g.fillStyle = mut(); g.font = "11px system-ui"; g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = y0 + (y1 - y0) * i / 4, y = Y(v);
    g.beginPath(); g.moveTo(pad, y); g.lineTo(w - 8, y); g.stroke();
    g.fillText(v.toFixed(v < 10 ? 2 : 0), 2, y + 4);
  }
  for (let i = 0; i <= 5; i++) {
    const t = x0 + (x1 - x0) * i / 5;
    g.fillText(fmtDur(t), X(t) - 12, h - 6);
  }
  for (const s of live) {
    g.strokeStyle = s.color; g.lineWidth = s.width || 1.6; g.beginPath();
    s.xs.forEach((x, i) => {
      const px = X(x), py = Y(s.ys[i]);
      if (i === 0) g.moveTo(px, py); else if (s.step) { g.lineTo(px, Y(s.ys[i - 1])); g.lineTo(px, py); } else g.lineTo(px, py);
    });
    g.stroke();
  }
  if (opts.target) for (const s of (R.segments || [])) if (s.target_w) {
    g.strokeStyle = "#f97316"; g.lineWidth = 2; g.beginPath();
    g.moveTo(X(s.start), Y(s.target_w)); g.lineTo(X(s.end), Y(s.target_w)); g.stroke();
  }
  const lg = legendId && document.getElementById(legendId);
  if (lg) lg.innerHTML = live.map(s => `<i style="background:${s.color}"></i>${s.label}`).join("");
}

const S = R.series;
plot("c_power", [
  {xs: S.power[0], ys: S.power[1], color: COL.a, label: "power"},
  {xs: S.target[0], ys: S.target[1], color: COL.b, label: "target", width: 1},
], "l_power", {zero: true, target: true});
plot("c_speed", [{xs: S.speed1s[0], ys: S.speed1s[1], color: COL.c, label: "speed"}], null, {zero: true});
plot("c_dist", [
  {xs: S.distance[0], ys: S.distance[1], color: COL.a, label: "distance m"},
  {xs: S.split[0], ys: S.split[1].map(v => v), color: COL.e, label: "split s", width: 1},
], "l_dist", {zero: true});
plot("c_spm", [
  {xs: S.spm[0], ys: S.spm[1], color: COL.d, label: "stroke rate"},
  {xs: S.hr[0], ys: S.hr[1], color: COL.b, label: "heart rate", width: 1},
], "l_spm", {zero: true});
// per stroke: drive and recovery durations, by stroke number
const n = Math.max(R.drives.length, R.recoveries.length);
const idx = [...Array(n).keys()].map(i => i + 1);
plot("c_strokes", [
  {xs: idx.slice(0, R.drives.length), ys: R.drives, color: COL.a, label: "drive s"},
  {xs: idx.slice(0, R.recoveries.length), ys: R.recoveries, color: COL.c, label: "recovery s"},
], "l_strokes", {zero: true});
(function planTable() {
  const rows = R.plan || [];
  if (!rows.length) { document.getElementById("plan_table").textContent = "no workout recorded"; return; }
  const f = v => v == null ? "–" : v;
  document.getElementById("plan_table").innerHTML =
    `<table style="width:100%;border-collapse:collapse;font-size:13px">` +
    `<tr style="color:var(--mut);text-align:left"><th>Interval</th><th>From</th><th>To</th><th>Target W</th>` +
    `<th>Mean W</th><th>Mean spm</th><th>Mean HR</th></tr>` +
    rows.map(r => `<tr><td>${r.kind}${r.rep ? " " + r.rep : ""}</td><td>${fmtDur(r.start)}</td>` +
      `<td>${fmtDur(r.end)}</td><td>${f(r.target_w)}</td><td>${f(r.mean_power_w)}</td>` +
      `<td>${f(r.mean_spm)}</td><td>${f(r.mean_hr)}</td></tr>`).join("") + "</table>";
})();
(function postprocessing() {
  const F = R.filters || {};
  const sel = document.getElementById("fsel");
  Object.keys(F).forEach(k => { const o = document.createElement("option"); o.value = k; o.textContent = k; sel.appendChild(o); });
  sel.value = "band 5 %, confirm 2 s";
  const draw = () => {
    const k = sel.value, f = F[k];
    if (!f) return;
    plot("c_fspeed", [
      {xs: S.speed1s[0], ys: S.speed1s[1], color: COL.c, label: "speed raw", width: 1},
      {xs: S.speed1s[0], ys: f.speed, color: COL.a, label: "filtered " + k},
    ], "l_fspeed", {zero: true});
    plot("c_fpower", [
      {xs: S.power1s[0], ys: S.power1s[1], color: COL.c, label: "power raw (1 s, cube law)", width: 1},
      {xs: S.power1s[0], ys: f.power, color: COL.a, label: "filtered " + k},
    ], "l_fpower", {zero: true});
    plot("c_fspm", [
      {xs: S.spm_raw[0], ys: S.spm_raw[1], color: COL.c, label: "stroke rate raw", width: 1},
      {xs: S.spm_raw[0], ys: f.spm, color: COL.a, label: "filtered " + k},
    ], "l_fspm", {zero: true});
  };
  sel.onchange = draw;
  draw();
})();
window.addEventListener("resize", () => location.reload());
</script>
</body></html>
"""


def build(folder: Path) -> Path:
    folder = Path(folder)
    session = _session(folder)
    data = summarise(folder, replay(folder, session), session)
    html = HTML.replace("__NAME__", data["name"]).replace("__DATA__", json.dumps(data))
    out = folder / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m rower.report <session folder>")
    print(build(Path(sys.argv[1])))
