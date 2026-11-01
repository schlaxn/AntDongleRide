// HUD: live metrics, phase banner and the workout timeline (gradient profile + cursor).

const $ = (id) => document.getElementById(id);

export function fmtClock(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? h + ":" : "") + mm + ":" + String(sec).padStart(2, "0");
}

const LABELS = {
  warmup: "WARM-UP", work: "WORK", recover: "RECOVER", between: "REST",
  cooldown: "COOL-DOWN", idle: "READY", paused: "PAUSED", finished: "DONE", none: "READY",
};

// live flyout (left side while riding): elapsed time counting up, interval time counting down,
// in the colour of the current interval
function renderFlyout(s) {
  $("fElapsed").textContent = fmtClock(s.elapsed_s);
  $("fLeft").textContent = s.phase_left_s != null ? fmtClock(s.phase_left_s) : "-";
  $("fLeft").className = "fv fv-left " + (s.phase || "");
}

export function renderSnapshot(s) {
  renderFlyout(s);
  const phase = s.phase || "none";
  const el = $("phaseName");
  el.textContent = LABELS[phase] || phase.toUpperCase();
  el.className = "phase " + phase;

  $("mHr").innerHTML = (s.hr_bpm || "-") + '<span class="u">bpm</span>';
  // real cadence sensor if present, otherwise the virtual cadence from virtual gearing
  const cadShown = s.cadence_rpm ? s.cadence_rpm : (s.virtual_cadence_rpm != null ? Math.round(s.virtual_cadence_rpm) : null);
  $("mCad").innerHTML = (cadShown || "-") + '<span class="u">rpm</span>';
  $("kCad").textContent = s.cadence_rpm ? "Cadence" : "Cadence (virtual)";
  // speed: two significant digits. 7.5 and 9.7 keep one decimal; 11, 23, 123 are whole numbers.
  const fmtSpeed = (v) => (v == null ? "-" : v < 10 ? v.toFixed(1) : String(Math.round(v)));
  // distance in km: three significant digits (0.07, 1.34, 12.3, 123)
  const fmtKm = (km) => (km < 1 ? km.toFixed(2) : km < 10 ? km.toFixed(2) : km < 100 ? km.toFixed(1) : String(Math.round(km)));
  $("mSpd").innerHTML = fmtSpeed(s.speed_kmh) + '<span class="u">km/h</span>';
  $("mVirt").innerHTML = fmtSpeed(s.virtual_speed_kmh) + '<span class="u">km/h</span>';
  $("mPow").innerHTML = (s.power_w != null ? s.power_w : "-") + '<span class="u">W</span>';
  // gear: the chainring/cog (e.g. 40/11) is the main number; the numeric ratio and development only in full mode
  const riding = document.body.classList.contains("riding");
  const ratio = s.gear_ratio != null ? s.gear_ratio.toFixed(2) : (s.virtual_gear ? s.virtual_gear_ratio.toFixed(2) : null);
  const cogs = s.virtual_cogs || null;
  let note = "";
  if (!riding) {
    if (cogs) note = `${ratio}`;
    else if (s.development_m != null) note = `${s.development_m} m/rev`;
  }
  $("mGear").innerHTML = (cogs || ratio || "-") + `<span class="u">${note}</span>`;
  $("mDist").innerHTML = fmtKm(s.virtual_distance_m / 1000) + '<span class="u">km</span>';
  $("mElapsed").textContent = fmtClock(s.elapsed_s);
  $("mGrad").innerHTML = (s.gradient_pct ?? 0).toFixed(1) + '<span class="u">%</span>';

  if (s.block) {
    $("blockInfo").textContent = `Block ${s.block} · rep ${s.rep}`;
  } else {
    $("blockInfo").textContent = "";
  }
  const left = s.phase_left_s ? `${Math.ceil(s.phase_left_s)} s left` : "";
  const next = s.next_phase && s.next_phase !== "finished" ? `next: ${LABELS[s.next_phase] || s.next_phase}` : "";
  $("phaseSub").textContent = [left, next].filter(Boolean).join(" · ") || (s.status === "idle" ? "Load a workout and press Start" : LABELS[phase] || "");
  $("nextInfo").textContent = s.next_phase && s.status !== "idle" ? "" : "";
}

export function setSensorChip(id, label, ageS) {
  const el = $(id);
  if (ageS == null) { el.textContent = `${label} -`; el.className = "pill"; return; }
  el.textContent = label;
  el.className = "pill " + (ageS < 3 ? "ok" : "bad");
}

// ---- timeline ----
let tl = null; // {segments, grad, total}

export function setTimeline(workout) {
  tl = {
    segments: workout.segments,
    grad: workout.gradient_per_second,
    total: workout.total_s,
  };
  $("tlTotal").textContent = fmtClock(workout.total_s);
  drawTimeline(0);
}

export function drawTimeline(elapsed) {
  const canvas = $("timeline");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!tl) return;

  const mid = h * 0.55, scale = (h * 0.42) / 12; // +/-12 % full height
  const x = (t) => (t / tl.total) * w;

  // phase bands along the bottom
  const bandY = h - 14;
  const colors = { warmup: "#e0b34055", work: "#f0654b88", recover: "#3ea6e055", between: "#8b93a140", cooldown: "#7ccf7a55" };
  for (const s of tl.segments) {
    ctx.fillStyle = colors[s.kind] || "#333";
    ctx.fillRect(x(s.start), bandY, Math.max(1, x(s.end) - x(s.start)), 10);
  }

  // gradient profile
  ctx.beginPath();
  ctx.moveTo(0, mid);
  tl.grad.forEach((g, i) => ctx.lineTo(x(i), mid - g * scale));
  ctx.lineTo(w, mid);
  ctx.closePath();
  ctx.fillStyle = "#ffffff14";
  ctx.fill();
  ctx.strokeStyle = "#c9d1d9";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  tl.grad.forEach((g, i) => (i ? ctx.lineTo(x(i), mid - g * scale) : ctx.moveTo(x(i), mid - g * scale)));
  ctx.stroke();

  // cursor
  const cx = x(Math.min(elapsed, tl.total));
  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(cx, 0);
  ctx.lineTo(cx, h);
  ctx.stroke();
}
