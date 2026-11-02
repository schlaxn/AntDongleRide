// HUD: tiles, phase banner, flyout and timeline. Reads the snapshot the server publishes.
const $ = (id) => document.getElementById(id);

const PHASE_LABEL = {
  warmup: "WARM-UP", work: "WORK", recover: "RECOVER", between: "REST", cooldown: "COOL-DOWN",
  preview: "PREVIEW", idle: "READY", paused: "PAUSED", finished: "DONE", none: "READY",
};
const PHASE_COLOR = {
  warmup: "#e0b340", work: "#f0654b", recover: "#3ea6e0", between: "#5c6470", cooldown: "#7ccf7a",
};

export function fmtClock(s) {
  if (s == null || !isFinite(s)) return "-";
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(sec)}` : `${m}:${p(sec)}`;
}

export function fmtSplit(s) {
  if (s == null || !isFinite(s)) return "-";
  const t = Math.round(s * 10) / 10;
  const m = Math.floor(t / 60), sec = (t - m * 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

// Replace only the leading value text of a tile; the unit span after it is kept.
function setVal(id, text) {
  const el = $(id);
  if (!el) return;
  let tn = el.firstChild;
  if (!tn || tn.nodeType !== Node.TEXT_NODE) {
    tn = document.createTextNode("");
    el.insertBefore(tn, el.firstChild);
  }
  tn.textContent = text == null ? "-" : String(text);
}

export function renderSnapshot(s) {
  const kind = s.phase || s.status;
  const label = PHASE_LABEL[kind] || String(kind).toUpperCase();
  $("phaseName").textContent = label;
  $("phaseName").className = "phase " + (PHASE_LABEL[kind] ? kind : "none");
  $("statusPill").textContent = s.status;
  const left = s.phase_left_s;
  const running = s.status === "running";
  $("fLeft").textContent = running && left != null ? fmtClock(Math.ceil(left)) : "-";
  $("fLeft").className = "fv fv-left " + (kind || "");
  $("fElapsed").textContent = fmtClock(s.elapsed_s);
  $("phaseSub").textContent = running && left != null
    ? `next ${PHASE_LABEL[s.next_phase] || s.next_phase}`
    : (s.status === "preview" ? "Row to see live numbers, press Start when ready" : "");
  $("blockInfo").textContent = s.block ? `block ${s.block} · rep ${s.rep}` : "";
  $("nextInfo").textContent = s.next_phase && s.next_phase !== "finished" ? `next: ${PHASE_LABEL[s.next_phase] || s.next_phase}` : "";

  setVal("tPow", s.power_w == null ? null : Math.round(s.power_w));
  setVal("tHr", s.hr_bpm == null ? null : Math.round(s.hr_bpm));
  setVal("tSpm", s.stroke_rate_spm == null ? null : Math.round(s.stroke_rate_spm));
  setVal("tSplit", fmtSplit(s.split_s));
  setVal("tVSpeed", s.virtual_speed_ms == null ? null : s.virtual_speed_ms.toFixed(2));
  setVal("tVDist", s.virtual_distance_m == null ? null : Math.round(s.virtual_distance_m));
  setVal("tElapsed", fmtClock(s.elapsed_s));
  setVal("tTgt", s.target_w);
  setVal("tDps", s.distance_per_stroke_m == null ? null : s.distance_per_stroke_m.toFixed(1));
  setVal("tStrokes", s.strokes);
}

export function setSensorChip(id, label, ageS) {
  const el = $(id);
  if (!el) return;
  el.textContent = label;
  el.className = "pill" + (ageS == null ? "" : ageS < 3 ? " ok" : " bad");
}

// workout: the server's workout dict (segments with kind/start/end) and targets (watts per kind)
export function setTimeline(workout) {
  return {
    segments: workout.segments || [],
    total: workout.total_s || 0,
    targets: workout.targets || {},
  };
}

export function drawTimeline(tl, elapsed) {
  const c = $("timeline");
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const total = tl.total || 1;
  // target watts as a step line per segment (scaled to 0-180 W over the height)
  const yOf = (watts) => h - 10 - (Math.min(180, Math.max(0, watts)) / 180) * (h - 20);
  for (const seg of tl.segments) {
    const x0 = (seg.start / total) * w, x1 = (seg.end / total) * w;
    g.fillStyle = PHASE_COLOR[seg.kind] || "#5c6470";
    g.globalAlpha = 0.22;
    g.fillRect(x0, 0, Math.max(1, x1 - x0), h);
    const tgt = tl.targets[seg.kind];
    if (tgt != null) {
      g.globalAlpha = 0.9;
      g.strokeStyle = PHASE_COLOR[seg.kind] || "#5c6470";
      g.lineWidth = 3;
      g.beginPath();
      g.moveTo(x0, yOf(tgt));
      g.lineTo(x1, yOf(tgt));
      g.stroke();
    }
  }
  g.globalAlpha = 1;
  if (elapsed != null) {
    const x = Math.min(w, (elapsed / total) * w);
    g.fillStyle = "#1d4ed8";   // blue cursor: readable on the light timeline
    g.fillRect(x - 1, 0, 2, h);
  }
}
