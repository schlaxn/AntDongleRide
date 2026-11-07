"""Static HTML report for the pedal test. Standard library only.

Usage:  python -I make_report.py [data folder]
The data folder holds capture_*.csv (raw ANT frames + ride markers) and samples_*.csv (one per ride) pulled from the
phone; default: <repo>/.claude/tmp/pedal_test (git-ignored: recordings are personal and must never be committed).
Output: report_<capture>.html in the data folder. Metrics exclude the first and last TRIM_S seconds of each ride.
Rides shorter than 60 s are ignored. The pedals are the device of type 11 that sends the most page 0xE2 frames.
"""
import bisect, collections, glob, html, json, math, os, statistics, sys

TRIM_S = 15.0
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = next((a for a in sys.argv[1:] if not a.startswith("--")),
            os.path.join(SCRIPT_DIR, "..", "..", ".claude", "tmp", "pedal_test"))


def read_capture(path):
    rides, markers, e2_all = [], [], []
    for line in open(path, errors="replace"):
        p = line.strip().split(",")
        if len(p) >= 2 and p[1] in ("RIDE_START", "RIDE_STOP"):
            markers.append((int(p[0]), p[1]))
            continue
        if len(p) < 2:
            continue
        try:
            ts = int(p[0])
            b = bytes.fromhex(p[1])
        except ValueError:
            continue
        if len(b) < 5 or b[0] != 0xA4 or len(b) != b[1] + 4:
            continue
        # broadcast with extended data: page at b[4], event/cycle count at b[5], devnum b[13..14], devtype b[15]
        if b[2] == 0x4E and len(b) >= 18 and (b[12] & 0x80):
            devnum = b[13] | (b[14] << 8)
            devtype = b[15]
            if devtype == 11 and b[4] == 0xE2:
                e2_all.append((ts, b[5], devnum))
    # the pedals: the power-meter device that sends the most 0xE2 frames
    best = collections.Counter(d for _, _, d in e2_all).most_common(1)
    e2 = [(ts, c) for ts, c, d in e2_all if best and d == best[0][0]]
    start = None
    for ts, m in markers:
        if m == "RIDE_START":
            start = ts
        elif m == "RIDE_STOP" and start is not None:
            rides.append((start, ts))
            start = None
    return rides, e2


def read_samples(path):
    out = collections.defaultdict(list)
    for line in open(path):
        p = line.strip().split(",")
        if len(p) != 5 or p[0] == "ts_ms":
            continue
        out[p[3]].append((int(p[0]), float(p[4])))
    for k in out:
        out[k].sort()
    return out


def stats(vals):
    if not vals:
        return None
    v = sorted(vals)
    return dict(n=len(v), mean=statistics.fmean(v), median=statistics.median(v), min=v[0], max=v[-1])


def fmt(x, d=1):
    return "-" if x is None else f"{x:.{d}f}"


def nearest(times, values, t, tol):
    i = bisect.bisect_left(times, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(times) and abs(times[j] - t) <= tol:
            if best is None or abs(times[j] - t) < abs(times[best] - t):
                best = j
    return None if best is None else values[best]


def trim_window(series, t0, t1):
    return [(t, v) for t, v in series if t0 <= t <= t1]


def svg_lines(title, series_list, dur, height=170, excl=TRIM_S):
    """series_list: [(label, color, [(t, v), ...])]"""
    w, h, pl, pr, pt, pb = 900, height + 18, 52, 12, 40, 22
    allv = [v for _, _, s in series_list for _, v in s]
    if not allv:
        return f"<p class='muted'>{html.escape(title)}: no data</p>"
    lo, hi = min(allv), max(allv)
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad
    X = lambda t: pl + (w - pl - pr) * t / dur
    Y = lambda v: pt + (h - pt - pb) * (1 - (v - lo) / (hi - lo))
    out = [f"<svg viewBox='0 0 {w} {h}' class='chart'>"]
    out.append(f"<text x='{pl}' y='14' class='ct'>{html.escape(title)}</text>")
    out.append(f"<rect x='{X(0):.1f}' y='{pt}' width='{X(excl)-X(0):.1f}' height='{h-pt-pb}' class='excl'/>")
    out.append(f"<rect x='{X(dur-excl):.1f}' y='{pt}' width='{X(dur)-X(dur-excl):.1f}' height='{h-pt-pb}' class='excl'/>")
    out.append(f"<line x1='{pl}' y1='{h-pb}' x2='{w-pr}' y2='{h-pb}' class='axis'/>")
    out.append(f"<text x='{pl-4}' y='{Y(hi)+4:.1f}' class='tick' text-anchor='end'>{hi:.0f}</text>")
    out.append(f"<text x='{pl-4}' y='{Y(lo)+4:.1f}' class='tick' text-anchor='end'>{lo:.0f}</text>")
    for sec in range(0, int(dur) + 1, 10):
        out.append(f"<text x='{X(sec):.1f}' y='{h-6}' class='tick' text-anchor='middle'>{sec}s</text>")
    step = max(1, len(series_list and series_list[0][2]) // 1200)
    lx = pl
    for label, color, s in series_list:
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in s[::step])
        if pts:
            out.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='1.2'/>")
        out.append(f"<text x='{lx}' y='32' class='leg' fill='{color}'>{html.escape(label)}</text>")
        lx += 24 + 8 * len(label)
    out.append("</svg>")
    return "".join(out)


def svg_scatter(title, xlabel, ylabel, pts, color="#2b6cb0"):
    w, h, pl, pr, pt, pb = 420, 300, 52, 12, 24, 40
    if len(pts) < 2:
        return f"<p class='muted'>{html.escape(title)}: not enough paired data</p>"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 - x0 < 1e-9: x1 = x0 + 1
    if y1 - y0 < 1e-9: y1 = y0 + 1
    X = lambda x: pl + (w - pl - pr) * (x - x0) / (x1 - x0)
    Y = lambda y: pt + (h - pt - pb) * (1 - (y - y0) / (y1 - y0))
    out = [f"<svg viewBox='0 0 {w} {h}' class='chart small'>"]
    out.append(f"<text x='{pl}' y='14' class='ct'>{html.escape(title)}</text>")
    out.append(f"<line x1='{pl}' y1='{h-pb}' x2='{w-pr}' y2='{h-pb}' class='axis'/>")
    out.append(f"<line x1='{pl}' y1='{pt}' x2='{pl}' y2='{h-pb}' class='axis'/>")
    for x in (x0, x1):
        out.append(f"<text x='{X(x):.1f}' y='{h-pb+14}' class='tick' text-anchor='middle'>{x:.0f}</text>")
    for y in (y0, y1):
        out.append(f"<text x='{pl-4}' y='{Y(y)+4:.1f}' class='tick' text-anchor='end'>{y:.0f}</text>")
    out.append(f"<text x='{(pl+w-pr)/2:.0f}' y='{h-6}' class='tick' text-anchor='middle'>{html.escape(xlabel)}</text>")
    out.append(f"<text x='12' y='{(pt+h-pb)/2:.0f}' class='tick' transform='rotate(-90 12 {(pt+h-pb)/2:.0f})' text-anchor='middle'>{html.escape(ylabel)}</text>")
    for x, y in pts:
        out.append(f"<circle cx='{X(x):.1f}' cy='{Y(y):.1f}' r='1.8' fill='{color}' fill-opacity='0.35'/>")
    out.append(f"<text x='{w-pr}' y='14' class='tick' text-anchor='end'>n={len(pts)}</text>")
    out.append("</svg>")
    return "".join(out)


def arc_points(a0, a1, cx, cy, r, steps=40):
    """Points along a clockwise arc from a0 to a1 (degrees, 0 = top, as in the live view)."""
    span = (a1 - a0) % 360.0
    pts = []
    for i in range(steps + 1):
        a = math.radians(a0 + span * i / steps - 90.0)
        pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
    return " ".join(pts)


def svg_crank(title, color, arcs, peaks):
    w = h = 240
    cx = cy = 120
    r = 80
    out = [f"<svg viewBox='0 0 {w} {h}' class='chart small'>"]
    out.append(f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='#cbd5e0' stroke-width='2'/>")
    out.append(f"<text x='{cx}' y='14' class='ct' text-anchor='middle'>{html.escape(title)}</text>")
    out.append(f"<text x='{cx}' y='{cy - r - 6}' class='tick' text-anchor='middle'>0</text>")
    out.append(f"<text x='{cx}' y='{cy + r + 14}' class='tick' text-anchor='middle'>180</text>")
    if not arcs and not peaks:
        out.append(f"<text x='{cx}' y='{cy}' class='tick' text-anchor='middle'>no data</text>")
    for s, e in arcs:
        out.append(f"<polyline points='{arc_points(s, e, cx, cy, r)}' fill='none' stroke='{color}' "
                   f"stroke-opacity='0.08' stroke-width='6'/>")
    for s, e in peaks:
        out.append(f"<polyline points='{arc_points(s, e, cx, cy, r - 8)}' fill='none' stroke='{color}' "
                   f"stroke-opacity='0.08' stroke-width='4' stroke-dasharray='3 3'/>")
    if arcs:
        ms = statistics.median([s for s, _ in arcs])
        me = statistics.median([e for _, e in arcs])
        out.append(f"<polyline points='{arc_points(ms, me, cx, cy, r)}' fill='none' stroke='{color}' stroke-width='8'/>")
        out.append(f"<text x='{cx}' y='{cy - 4}' class='tick' text-anchor='middle'>median {ms % 360:.0f}°–{me % 360:.0f}°</text>")
    if peaks:
        ps = statistics.median([s for s, _ in peaks])
        pe = statistics.median([e for _, e in peaks])
        out.append(f"<polyline points='{arc_points(ps, pe, cx, cy, r - 8)}' fill='none' stroke='{color}' "
                   f"stroke-width='5' stroke-dasharray='3 3'/>")
    out.append("</svg>")
    return "".join(out)


def svg_bars(cycles):
    if not cycles:
        return ""
    w, h, pl, pr, pt, pb = 900, 190, 52, 12, 24, 30
    pmax = max([c["ppeak"] or 0 for c in cycles] + [1])
    n = len(cycles)
    bw = (w - pl - pr) / n
    plot_h = h - pt - pb
    out = [f"<svg viewBox='0 0 {w} {h}' class='chart'>"]
    out.append(f"<text x='{pl}' y='14' class='ct'>Power per crank cycle: average (bar) and peak (orange line), W</text>")
    out.append(f"<line x1='{pl}' y1='{h-pb}' x2='{w-pr}' y2='{h-pb}' class='axis'/>")
    out.append(f"<text x='{pl-4}' y='{pt+4}' class='tick' text-anchor='end'>{pmax:.0f}</text>")
    out.append(f"<text x='{pl-4}' y='{h-pb}' class='tick' text-anchor='end'>0</text>")
    for i, c in enumerate(cycles):
        x = pl + i * bw
        pav = c["pavg"] or 0
        bh = plot_h * pav / pmax
        out.append(f"<rect x='{x+2:.1f}' y='{h-pb-bh:.1f}' width='{max(bw-4, 1):.1f}' height='{bh:.1f}' fill='#2b6cb0'/>")
        if c["ppeak"]:
            py = h - pb - plot_h * c["ppeak"] / pmax
            out.append(f"<line x1='{x+2:.1f}' y1='{py:.1f}' x2='{x+bw-2:.1f}' y2='{py:.1f}' stroke='#dd6b20' stroke-width='2'/>")
        out.append(f"<text x='{x+bw/2:.1f}' y='{h-pb+14}' class='tick' text-anchor='middle'>{i+1}</text>")
    out.append("</svg>")
    return "".join(out)


def ride_section(idx, ride, samples, e2, capture_name):
    t0, t1 = ride
    dur = (t1 - t0) / 1000.0
    S = lambda m: [((ts - t0) / 1000.0, v) for ts, v in samples.get(m, []) if t0 <= ts <= t1]
    speed, scad, cad, pw = S("speed_kmh"), S("sensor_cadence"), S("cadence"), S("power")
    hr = S("heart_rate")
    bal, pl_, pr_ = S("pedal_balance_right"), S("pco_left"), S("pco_right")
    te_l, te_r, ps_l, ps_r = S("torque_eff_left"), S("torque_eff_right"), S("pedal_smooth_left"), S("pedal_smooth_right")
    fa = {k: S(k) for k in ("fa_left_start_deg", "fa_left_end_deg", "fa_left_peak_start_deg", "fa_left_peak_end_deg",
                              "fa_right_start_deg", "fa_right_end_deg", "fa_right_peak_start_deg", "fa_right_peak_end_deg",
                              "fa_left_torque_nm", "fa_right_torque_nm")}
    lo_t, hi_t = TRIM_S, dur - TRIM_S
    tw = lambda s: trim_window(s, lo_t, hi_t)

    # derived per-pedal power from the balance (same frame as power) and nearest power
    p_t = [t for t, _ in pw]
    p_v = [v for _, v in pw]
    left_w, right_w = [], []
    for t, share in bal:
        p = nearest(p_t, p_v, t, 0.6)
        if p is not None:
            left_w.append((t, p * (100 - share) / 100.0))
            right_w.append((t, p * share / 100.0))

    # power cycles from the pedal's crank-cycle counter (E2 event count), unwrapped mod 256
    changes = []
    last = None
    for ts, c in e2:
        if not (t0 <= ts <= t1):
            continue
        if last is None or c != last:
            changes.append(((ts - t0) / 1000.0, c))
            last = c
    cycles = []
    for (a, _), (b, _) in zip(changes, changes[1:]):
        if a < lo_t or b > hi_t:
            continue
        seg_p = [v for t, v in pw if a <= t < b]
        seg_b = [v for t, v in bal if a <= t < b]
        seg_c = [v for t, v in cad if a <= t < b]
        seg_pco = [v for t, v in pl_ if a <= t < b]
        d = b - a
        cycles.append(dict(
            start=a, dur=d, cad=60.0 / d if d > 0 else None,
            pavg=statistics.fmean(seg_p) if seg_p else None,
            ppeak=max(seg_p) if seg_p else None,
            bal=statistics.fmean(seg_b) if seg_b else None,
            cad_meas=statistics.fmean(seg_c) if seg_c else None,
        ))

    # metrics table (trimmed window)
    rows = []
    def row(label, unit, vals):
        s = stats(vals)
        if s:
            rows.append(f"<tr><td>{label}</td><td>{unit}</td><td>{s['n']}</td><td>{s['mean']:.1f}</td>"
                        f"<td>{s['median']:.1f}</td><td>{s['min']:.1f}</td><td>{s['max']:.1f}</td></tr>")
        else:
            rows.append(f"<tr><td>{label}</td><td>{unit}</td><td>0</td><td colspan='4' class='muted'>no data</td></tr>")
    row("Heart rate", "bpm", [v for _, v in tw(hr)])
    row("Speed", "km/h", [v for _, v in tw(speed)])
    row("Cadence (pedals)", "rpm", [v for _, v in tw(cad)])
    row("Cadence (speed/cadence sensor)", "rpm", [v for _, v in tw(scad)])
    row("Power, total", "W", [v for _, v in tw(pw)])
    row("Power, left pedal", "W", [v for _, v in tw(left_w)])
    row("Power, right pedal", "W", [v for _, v in tw(right_w)])
    row("Balance, right share", "%", [v for _, v in tw(bal)])
    row("Platform center offset, left", "mm", [v for _, v in tw(pl_)])
    row("Platform center offset, right", "mm", [v for _, v in tw(pr_)])
    row("Torque effectiveness, left", "%", [v for _, v in tw(te_l)])
    row("Torque effectiveness, right", "%", [v for _, v in tw(te_r)])
    row("Pedal smoothness, left", "%", [v for _, v in tw(ps_l)])
    row("Pedal smoothness, right", "%", [v for _, v in tw(ps_r)])
    for side in ("left", "right"):
        row(f"Power phase {side}: start", "deg", [v for _, v in tw(fa[f'fa_{side}_start_deg'])])
        row(f"Power phase {side}: end", "deg", [v for _, v in tw(fa[f'fa_{side}_end_deg'])])
        row(f"Peak torque window {side}: start", "deg", [v for _, v in tw(fa[f'fa_{side}_peak_start_deg'])])
        row(f"Peak torque window {side}: end", "deg", [v for _, v in tw(fa[f'fa_{side}_peak_end_deg'])])
        row(f"Torque {side} (averaged over the event)", "Nm", [v for _, v in tw(fa[f'fa_{side}_torque_nm'])])

    # scatter pairs inside the trimmed window
    tp = [t for t, _ in pw]
    vp = [v for _, v in pw]
    tc = [t for t, _ in cad]
    vc = [v for _, v in cad]
    def pair_with(series, other_t, other_v, tol):
        out = []
        for t, v in tw(series):
            o = nearest(other_t, other_v, t, tol)
            if o is not None:
                out.append((o, v))
        return out
    sc = []
    sc.append(svg_scatter("Right balance vs power", "power (W)", "right share (%)",
                          pair_with(bal, tp, vp, 0.6) and [(y, x) for x, y in pair_with(bal, tp, vp, 0.6)]))
    sc.append(svg_scatter("PCO right vs power", "power (W)", "PCO right (mm)", pair_with(pr_, tp, vp, 1.0)))
    sc.append(svg_scatter("PCO left vs power", "power (W)", "PCO left (mm)", pair_with(pl_, tp, vp, 1.0)))
    sc.append(svg_scatter("PCO right vs cadence", "cadence (rpm)", "PCO right (mm)", pair_with(pr_, tc, vc, 1.0)))
    sc.append(svg_scatter("PCO left vs cadence", "cadence (rpm)", "PCO left (mm)", pair_with(pl_, tc, vc, 1.0)))
    sc.append(svg_scatter("Right balance vs cadence", "cadence (rpm)", "right share (%)", pair_with(bal, tc, vc, 0.6)))

    # time series charts (full ride; grey = excluded)
    charts = [
        svg_lines("Heart rate", [("heart rate bpm", "#c53030", hr)], dur),
        svg_lines("Power and cadence", [("power W", "#c53030", pw), ("cadence rpm", "#2b6cb0", cad)], dur),
        svg_lines("Speed and sensor cadence", [("speed km/h", "#2f855a", speed), ("sensor cadence rpm", "#805ad5", scad)], dur),
        svg_lines("Balance and per-pedal power", [("right share %", "#dd6b20", bal), ("left W", "#2b6cb0", left_w), ("right W", "#c53030", right_w)], dur),
        svg_lines("Platform center offset", [("PCO left mm", "#2b6cb0", pl_), ("PCO right mm", "#dd6b20", pr_)], dur),
        svg_lines("Torque effectiveness and pedal smoothness",
                  [("TE left %", "#2b6cb0", te_l), ("TE right %", "#dd6b20", te_r),
                   ("smooth left %", "#90cdf4", ps_l), ("smooth right %", "#fbd38d", ps_r)], dur),
        svg_lines("Power phase, crank angles", [("L start", "#2b6cb0", fa["fa_left_start_deg"]), ("L end", "#90cdf4", fa["fa_left_end_deg"]),
                                               ("R start", "#dd6b20", fa["fa_right_start_deg"]), ("R end", "#fbd38d", fa["fa_right_end_deg"])], dur),
    ]

    cyc_rows = "".join(
        f"<tr><td>{i+1}</td><td>{c['start']:.1f}</td><td>{c['dur']:.2f}</td><td>{fmt(c['cad'],0)}</td>"
        f"<td>{fmt(c['pavg'],0)}</td><td>{fmt(c['ppeak'],0)}</td><td>{fmt(c['bal'],0)}</td></tr>"
        for i, c in enumerate(cycles))
    cyc_table = (f"<table><tr><th>#</th><th>start (s)</th><th>duration (s)</th><th>cadence from cycle (rpm)</th>"
                 f"<th>avg power (W)</th><th>peak power (W)</th><th>right share (%)</th></tr>{cyc_rows}</table>"
                 if cycles else "<p class='muted'>No complete power cycles inside the trimmed window.</p>")
    cyc_stats = ""
    if cycles:
        cp = [c["pavg"] for c in cycles if c["pavg"] is not None]
        cd = [c["cad"] for c in cycles if c["cad"] is not None]
        cyc_stats = (f"<p>{len(cycles)} cycles. Average cycle power {fmt(statistics.fmean(cp),0)} W "
                     f"(range {fmt(min(cp),0)}–{fmt(max(cp),0)}), cadence from cycle durations "
                     f"{fmt(statistics.fmean(cd),0)} rpm (range {fmt(min(cd),0)}–{fmt(max(cd),0)}).</p>")

    note = "" if hi_t - lo_t >= 30 else (
        f"<p class='warn'>Only {max(0, hi_t - lo_t):.0f} s remain after trimming this ride ({dur:.0f} s long), "
        f"so these statistics are weak.</p>")

    def pairs(a, b):
        db = dict(tw(b))
        return [(v, db[t]) for t, v in tw(a) if t in db]

    arcs_l = pairs(fa["fa_left_start_deg"], fa["fa_left_end_deg"])
    arcs_r = pairs(fa["fa_right_start_deg"], fa["fa_right_end_deg"])
    peaks_l = pairs(fa["fa_left_peak_start_deg"], fa["fa_left_peak_end_deg"])
    peaks_r = pairs(fa["fa_right_peak_start_deg"], fa["fa_right_peak_end_deg"])
    crank_html = (
        "<div class='grid'>"
        + svg_crank("Left pedal: power phase", "#2b6cb0", arcs_l, peaks_l)
        + svg_crank("Right pedal: power phase", "#dd6b20", arcs_r, peaks_r)
        + "</div>")

    # The pedals do not report individual crank cycles: each side sends one update about every 2 s (average
    # angles and torque), and the counter step per update (median 2) is not the number of crank turns.
    def med(vals):
        return statistics.median(vals) if vals else None
    def arc(pr):
        return med([(e - s) % 360.0 for s, e in pr])
    cmp_rows = [
        ("Power phase start (deg)", med([s for s, _ in arcs_l]), med([s for s, _ in arcs_r])),
        ("Power phase end (deg)", med([e for _, e in arcs_l]), med([e for _, e in arcs_r])),
        ("Power phase length (deg)", arc(arcs_l), arc(arcs_r)),
        ("Peak torque window start (deg)", med([s for s, _ in peaks_l]), med([s for s, _ in peaks_r])),
        ("Peak torque window end (deg)", med([e for _, e in peaks_l]), med([e for _, e in peaks_r])),
        ("Peak torque window width (deg)", arc(peaks_l), arc(peaks_r)),
        ("Torque per update (Nm)", med([v for _, v in tw(fa["fa_left_torque_nm"])]), med([v for _, v in tw(fa["fa_right_torque_nm"])])),
        ("Torque effectiveness (%)", med([v for _, v in tw(te_l)]), med([v for _, v in tw(te_r)])),
        ("Pedal smoothness (%)", med([v for _, v in tw(ps_l)]), med([v for _, v in tw(ps_r)])),
        ("Platform center offset (mm)", med([v for _, v in tw(pl_)]), med([v for _, v in tw(pr_)])),
    ]
    cmp_html = ("<table><tr><th>median per side</th><th>left</th><th>right</th></tr>"
                + "".join(f"<tr><td>{n}</td><td>{fmt(l)}</td><td>{fmt(r)}</td></tr>" for n, l, r in cmp_rows)
                + "</table>")
    # balance by power band: right share and the left-minus-right power difference
    band_rows = []
    bal_by_t = dict(tw(bal))
    for a, b in ((0, 100), (100, 150), (150, 200), (200, 250), (250, 300), (300, 400), (400, 5000)):
        sel = [(p, bal_by_t[t]) for t, p in tw(pw) if a <= p < b and t in bal_by_t and p > 0]
        if len(sel) < 20:
            continue
        r = statistics.fmean(x for _, x in sel)
        pm = statistics.fmean(p for p, _ in sel)
        band_rows.append(f"<tr><td>{a}–{b if b < 5000 else '+'} W</td><td>{len(sel)}</td><td>{r:.1f}</td>"
                         f"<td>{100 - r:.1f}</td><td>{pm * (100 - 2 * r) / 100:+.1f}</td></tr>")
    band_html = ("<table><tr><th>power band</th><th>frames</th><th>right share (%)</th><th>left share (%)</th>"
                 "<th>left minus right (W)</th></tr>" + "".join(band_rows) + "</table>")
    cyc_stats = ("<p class='muted'>The pedals send one update per side about every 2 s (average angles and torque over "
                 "their internal interval). Individual crank cycles are not reported, so no per-cycle values are shown.</p>")
    cyc_table = cmp_html + "<h3>Balance by power band</h3>" + band_html

    return f"""
<section>
<h2>Ride {idx+1}: {dur:.1f} s (metrics for {lo_t:.0f}–{hi_t:.0f} s)</h2>
{note}
<h3>Metrics (trimmed window)</h3>
<table><tr><th>metric</th><th>unit</th><th>n</th><th>mean</th><th>median</th><th>min</th><th>max</th></tr>{''.join(rows)}</table>
<h3>Power phase and left vs right (per-side updates about every 2 s)</h3>
{crank_html}
{cyc_stats}{cyc_table}
<h3>Scatter plots (trimmed window)</h3>
<div class='grid'>{''.join(sc)}</div>
<h3>Over time (grey = excluded first and last {TRIM_S:.0f} s)</h3>
{''.join(charts)}
</section>"""


def main():
    capture = sorted(glob.glob(os.path.join(DATA, "capture_*.csv")))[-1]
    rides, e2 = read_capture(capture)
    # very short start/stop pairs (under 60 s) are test presses, not rides
    rides = [r for r in rides if (r[1] - r[0]) > 60000]
    sample_files = sorted(glob.glob(os.path.join(DATA, "samples_*.csv")))
    sections = []
    for i, ride in enumerate(rides):
        # samples file for this ride: the one whose name matches the ride start time (HHMMSS)
        match = None
        stamp = __import__("datetime").datetime.fromtimestamp(ride[0] / 1000).strftime("%H%M%S")
        for f in sample_files:
            if os.path.basename(f).split("_")[-1].split(".")[0].startswith(stamp[:4]):
                match = f
        samples = read_samples(match) if match else collections.defaultdict(list)
        sections.append(ride_section(i, ride, samples, e2, os.path.basename(capture)))
    head = f"""<!doctype html><html><head><meta charset='utf-8'><title>Pedal test report</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1a202c;background:#fff;max-width:1000px}}
h1{{margin-bottom:4px}} h2{{border-bottom:2px solid #cbd5e0;padding-bottom:4px;margin-top:36px}}
table{{border-collapse:collapse;margin:8px 0;font-size:13px}} th,td{{border:1px solid #e2e8f0;padding:3px 8px;text-align:right}}
td:first-child,th:first-child{{text-align:left}} .muted{{color:#718096}} .warn{{color:#c05621;font-weight:600}}
svg.chart{{width:100%;height:auto;margin:6px 0}} svg.small{{width:100%}} .ct{{font-size:13px;font-weight:600}}
.tick{{font-size:10px;fill:#4a5568}} .leg{{font-size:11px;font-weight:600}} .axis{{stroke:#4a5568;stroke-width:1}}
.excl{{fill:#edf2f7}} .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:8px}}
</style></head><body>
<h1>Pedal test report</h1>
<p class='muted'>Source: {html.escape(os.path.basename(capture))}. Metrics exclude the first and last {TRIM_S:.0f} s of each ride.
Power per pedal = total power × balance share (same pedal frame). The pedals do not report individual crank cycles:
power phase, torque, effectiveness and smoothness per side are averages the pedal sends about every 2 s.</p>
"""
    body = "".join(sections) if sections else "<p>No rides found in the capture.</p>"
    out = os.path.join(DATA, "report_" + os.path.basename(capture).replace(".csv", "") + ".html")
    open(out, "w", encoding="utf-8").write(head + body + "</body></html>")
    print("written:", out)


if __name__ == "__main__":
    main()
