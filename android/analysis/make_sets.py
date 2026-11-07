"""Two sets of crank diagrams: the hard work repetitions vs one easy section.

Usage:  python -I make_sets.py [data folder] [--easy=MM:SS-MM:SS]
Standard library only. Reuses helpers from make_report.py (same folder). Reads capture_*.csv and samples_*.csv
from the data folder (default: <repo>/.claude/tmp/pedal_test, git-ignored) and writes report_sets_<capture>.html there.
Work repetitions are detected from the power (smoothed power above WORK_ON until it falls below WORK_OFF, 15-45 s,
mean above 190 W: tuned for interval sets of about 200-250 W; adjust the thresholds for other sessions). The easy
section defaults to 21:00-30:00 of the ride and is set with --easy=.
"""
import glob, html, importlib.util, math, os, statistics as st, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mr", os.path.join(SCRIPT_DIR, "make_report.py"))
mr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr)
DATA = mr.DATA

TRIM = 15            # first and last seconds of the ride that are ignored
WORK_ON, WORK_OFF = 175.0, 150.0   # smoothed power thresholds that open / close a work repetition
UPDATE_S = 2.5       # a per-side update averages roughly the 2.5 s before its arrival


def _easy_range():
    """The easy section in seconds from the ride start: --easy=MM:SS-MM:SS, default 21:00-30:00."""
    for a in sys.argv[1:]:
        if a.startswith("--easy="):
            f, t = a[len("--easy="):].split("-")
            sec = lambda x: int(x.split(":")[0]) * 60 + int(x.split(":")[1])
            return sec(f), sec(t)
    return 21 * 60, 30 * 60


EASY_FROM, EASY_TO = _easy_range()


def main():
    capture = sorted(glob.glob(os.path.join(DATA, "capture_*.csv")))[-1]
    rides, _ = mr.read_capture(capture)
    t0, t1 = [r for r in rides if (r[1] - r[0]) > 60000][0]
    samples = mr.read_samples(sorted(glob.glob(os.path.join(DATA, "samples_*.csv")))[-1])
    dur = int((t1 - t0) / 1000)
    S = lambda m: [((ts - t0) / 1000.0, v) for ts, v in samples.get(m, []) if t0 <= ts <= t1]

    # ---- per-second power, smoothed ----
    per = {}
    for t, v in S("power"):
        per.setdefault(int(t), []).append(v)
    avg = [st.fmean(per[i]) if i in per else None for i in range(dur + 1)]
    def rolling(i, half):
        w = [a for a in avg[max(0, i - half): i + half + 1] if a is not None]
        return st.fmean(w) if w else 0.0
    sm = [rolling(i, 1) for i in range(dur + 1)]
    m20 = [rolling(i, 10) for i in range(dur + 1)]

    # ---- detect the work repetitions ----
    segs, start = [], None
    for i, v in enumerate(sm):
        if start is None and v > WORK_ON:
            start = i
        elif start is not None and v < WORK_OFF:
            segs.append([start, i]); start = None
    merged = []
    for s, e in segs:
        if merged and s - merged[-1][1] < 5:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    reps = []
    for s, e in merged:
        d = e - s
        pm = st.fmean([a for a in avg[s:e] if a is not None] or [0])
        if 15 <= d <= 45 and pm > 190 and s >= TRIM and e <= dur - TRIM:
            reps.append(dict(start=s, end=e, dur=d, power=pm))
    # blocks: repetitions with gaps below 60 s belong together
    blocks = []
    for r in reps:
        if blocks and r["start"] - blocks[-1][-1]["end"] < 60:
            blocks[-1].append(r)
        else:
            blocks.append([r])

    # ---- the two sets, as sets of whole seconds ----
    work = set()
    for r in reps:
        work.update(range(r["start"] + 5, r["end"]))      # skip the first 5 s of a repetition (ramp up)
    in_block = set()
    for b in blocks:
        in_block.update(range(b[0]["start"] - 10, b[-1]["end"] + 10))
    last_end = reps[-1]["end"] if reps else dur
    # the easy set is one fixed section: the recovery between the two blocks, 21:00 to 30:00
    easy = set(range(max(TRIM, EASY_FROM), min(dur - TRIM, EASY_TO) + 1))

    def ranges(secs):
        out, run = [], None
        for i in sorted(secs):
            if run and i == run[1] + 1:
                run[1] = i
            else:
                if run: out.append(tuple(run))
                run = [i, i]
        if run: out.append(tuple(run))
        return [(a, b) for a, b in out if b - a >= 5]

    # ---- assign per-side updates to a set (the update averages the ~2.5 s before it arrives) ----
    def in_set(t, secs):
        first, last = math.ceil(t - UPDATE_S), math.floor(t)
        return all(i in secs for i in range(first, last + 1))

    def collect(secs):
        d = lambda m: {t: v for t, v in S(m) if in_set(t, secs)}
        fa = {k: d(k) for k in ("fa_left_start_deg", "fa_left_end_deg", "fa_left_peak_start_deg", "fa_left_peak_end_deg",
                                "fa_right_start_deg", "fa_right_end_deg", "fa_right_peak_start_deg", "fa_right_peak_end_deg",
                                "fa_left_torque_nm", "fa_right_torque_nm")}
        other = {k: d(k) for k in ("torque_eff_left", "torque_eff_right", "pedal_smooth_left", "pedal_smooth_right", "pco_left", "pco_right")}
        pw = [v for t, v in S("power") if int(t) in secs]
        bal = [v for t, v in S("pedal_balance_right") if int(t) in secs]
        cad = [v for t, v in S("cadence") if int(t) in secs]
        def pairs(a, b):
            return [(va, fa[b][t]) for t, va in fa[a].items() if t in fa[b]]
        unwrap = lambda s: s if s >= 180 else s + 360       # start angles straddle 0/360
        arcs = {side: [(unwrap(s), e) for s, e in pairs(f"fa_{side}_start_deg", f"fa_{side}_end_deg")] for side in ("left", "right")}
        peaks = {side: pairs(f"fa_{side}_peak_start_deg", f"fa_{side}_peak_end_deg") for side in ("left", "right")}
        return dict(fa=fa, other=other, power=pw, balance=bal, cadence=cad, arcs=arcs, peaks=peaks, seconds=len(secs))

    sets = {"Work repetitions (hard)": collect(work), "In-between recovery 21:00–30:00 (easy)": collect(easy)}
    for name, c in sets.items():
        print(f"{name}: {c['seconds']} s, mean power {st.fmean(c['power']):.0f} W, "
              f"per-side updates used left={len(c['arcs']['left'])} right={len(c['arcs']['right'])}")

    med = lambda v: st.median(v) if v else None
    fmt = lambda x, d=1: "-" if x is None else f"{x:.{d}f}"
    def table(name, c):
        a, p, f, o = c["arcs"], c["peaks"], c["fa"], c["other"]
        arc = lambda pr: med([(e - s) % 360 for s, e in pr])
        rows = [
            ("Power phase start (deg)", med([s for s, _ in a["left"]]), med([s for s, _ in a["right"]])),
            ("Power phase end (deg)", med([e for _, e in a["left"]]), med([e for _, e in a["right"]])),
            ("Power phase length (deg)", arc(a["left"]), arc(a["right"])),
            ("Peak window start (deg)", med([s for s, _ in p["left"]]), med([s for s, _ in p["right"]])),
            ("Peak window end (deg)", med([e for _, e in p["left"]]), med([e for _, e in p["right"]])),
            ("Peak window width (deg)", arc(p["left"]), arc(p["right"])),
            ("Torque per update (Nm)", med(list(f["fa_left_torque_nm"].values())), med(list(f["fa_right_torque_nm"].values()))),
            ("Torque effectiveness (%)", med(list(o["torque_eff_left"].values())), med(list(o["torque_eff_right"].values()))),
            ("Pedal smoothness (%)", med(list(o["pedal_smooth_left"].values())), med(list(o["pedal_smooth_right"].values()))),
            ("Platform center offset (mm)", med(list(o["pco_left"].values())), med(list(o["pco_right"].values()))),
            ("Updates used (n)", len(a["left"]), len(a["right"])),
        ]
        body = "".join(f"<tr><td>{n}</td><td>{fmt(l, 0 if 'Updates' in n else 1)}</td><td>{fmt(r, 0 if 'Updates' in n else 1)}</td></tr>" for n, l, r in rows)
        info = (f"<p>{c['seconds']} s of data. Mean power {fmt(st.fmean(c['power']) if c['power'] else None, 0)} W, "
                f"mean cadence {fmt(st.fmean(c['cadence']) if c['cadence'] else None, 0)} rpm, "
                f"right share {fmt(st.fmean(c['balance']) if c['balance'] else None, 1)} %.</p>")
        return info + f"<table><tr><th>median per side</th><th>left</th><th>right</th></tr>{body}</table>"

    def mmss(s): return f"{s // 60}:{s % 60:02d}"
    rep_rows = "".join(
        f"<tr><td>{bi + 1}</td><td>{ri + 1}</td><td>{mmss(r['start'])}</td><td>{r['dur']}</td><td>{r['power']:.0f}</td></tr>"
        for bi, b in enumerate(blocks) for ri, r in enumerate(b))
    easy_rows = "".join(f"<tr><td>{mmss(a)}–{mmss(b)}</td><td>{b - a}</td></tr>" for a, b in ranges(easy))

    sections = []
    for name, c in sets.items():
        sections.append(f"<h2>{html.escape(name)}</h2>" + table(name, c) +
                        "<div class='grid'>" + mr.svg_crank("Left pedal: power phase", "#2b6cb0", c["arcs"]["left"], c["peaks"]["left"])
                        + mr.svg_crank("Right pedal: power phase", "#dd6b20", c["arcs"]["right"], c["peaks"]["right"]) + "</div>")

    css = ("body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1a202c;max-width:1000px}"
           "table{border-collapse:collapse;margin:8px 0;font-size:13px}th,td{border:1px solid #e2e8f0;padding:3px 8px;text-align:right}"
           "td:first-child,th:first-child{text-align:left}.muted{color:#718096}svg.small{width:100%}.ct{font-size:13px;font-weight:600}"
           ".tick{font-size:10px;fill:#4a5568}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:8px}")
    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>Work vs easy: power phase</title><style>{css}</style></head><body>"
            f"<h1>Power phase: work repetitions vs warm-up / in-between</h1>"
            f"<p class='muted'>Source: {html.escape(os.path.basename(capture))}, ride of {dur} s. First and last {TRIM} s ignored. "
            f"Crank diagrams: 0° at the top, clockwise; faint arcs = every per-side update, solid = median, dashed = peak-torque window. "
            f"Each per-side update averages about the last {UPDATE_S:.1f} s, so only updates lying completely inside a set are used.</p>"
            f"<h2>Detected work repetitions ({len(reps)} in {len(blocks)} blocks)</h2>"
            f"<p class='muted'>Rule: smoothed power above {WORK_ON:.0f} W until it falls below {WORK_OFF:.0f} W, length 15–45 s, mean above 190 W. "
            f"The first 5 s of every repetition are skipped (ramp up).</p>"
            f"<table><tr><th>block</th><th>rep</th><th>start</th><th>length (s)</th><th>mean power (W)</th></tr>{rep_rows}</table>"
            f"<h2>In-between section used</h2>"
            f"<p class='muted'>One fixed section: the recovery between the two blocks, {mmss(EASY_FROM)} to {mmss(EASY_TO)}.</p>"
            f"<table><tr><th>time range</th><th>length (s)</th></tr>{easy_rows}</table>"
            + "".join(sections) + "</body></html>")
    out = os.path.join(DATA, "report_sets_" + os.path.basename(capture).replace(".csv", "") + ".html")
    open(out, "w", encoding="utf-8").write(page)
    print("repetitions per block:", [len(b) for b in blocks], "| total reps:", len(reps))
    print("easy seconds:", len(easy), "in ranges:", [(mmss(a), mmss(b)) for a, b in ranges(easy)])
    print("work seconds:", len(work))
    print("written:", out)


if __name__ == "__main__":
    main()
