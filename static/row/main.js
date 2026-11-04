// Virtual Row controller: a sibling of the ride page. Same flow (setup, countdown, riding
// layout, audio cues, Wake Lock), rowing data (power, split, stroke rate) and rowing targets.
import { beepCountdown, beepPhaseChange, unlockAudio } from "/static/ride/audio.js";
import { drawTimeline, fmtClock, renderSnapshot, setSensorChip, setTimeline } from "./hud.js";
import { createRowWorld } from "./world.js";
import { openSensorDialog, refresh as refreshSensors, sensorsLive, wireSensors } from "./sensors.js";

const $ = (id) => document.getElementById(id);
let world = null;
let status = "preview";
let timeline = { segments: [], total: 0, targets: {} };
let lastElapsed = 0;
let lastPhaseKey = null;
let lastCountdown = null;
let countdownActive = false;
let rowerReady = false;

const api = async (url, body) => {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
};

// ---------- workout editor ----------
const PRESETS = {
  quick: { blocks: 1, reps: 3, on_s: 20, off_s: 10, between_s: 0, warmup_s: 15, cooldown_s: 15, skip: true,
           targets: { warmup: 90, work: 150, recover: 70, between: 50, cooldown: 80 } },
  std: { blocks: 3, reps: 13, on_s: 30, off_s: 15, between_s: 300, warmup_s: 600, cooldown_s: 600, skip: true,
         targets: { warmup: 90, work: 150, recover: 70, between: 50, cooldown: 80 } },
};

function setWorkoutFields(p) {
  $("wBlocks").value = p.blocks;
  $("wReps").value = p.reps;
  $("wOn").value = p.on_s;
  $("wOff").value = p.off_s;
  $("wBetween").value = p.between_s;
  $("wWarm").value = p.warmup_s;
  $("wCool").value = p.cooldown_s;
  $("wSkip").checked = !!p.skip_last_recover;
  const t = p.targets || {};
  if (t.warmup != null) $("tWarm").value = t.warmup;
  if (t.work != null) $("tWork").value = t.work;
  if (t.recover != null) $("tRec").value = t.recover;
  if (t.between != null) $("tBetween").value = t.between;
  if (t.cooldown != null) $("tCool").value = t.cooldown;
}

function workoutBody() {
  return {
    blocks: +$("wBlocks").value,
    reps: +$("wReps").value,
    on_s: +$("wOn").value,
    off_s: +$("wOff").value,
    warmup_s: +$("wWarm").value,
    cooldown_s: +$("wCool").value,
    between_s: +$("wBetween").value,
    skip_last_recover: $("wSkip").checked,
    targets: {
      warmup: +$("tWarm").value, work: +$("tWork").value, recover: +$("tRec").value,
      between: +$("tBetween").value, cooldown: +$("tCool").value,
    },
  };
}

async function loadWorkout() {
  try {
    const w = await api("/api/row/workout", workoutBody());
    timeline = setTimeline(w);
    world?.setWorkout?.(w);
    $("loadInfo").textContent = `${w.blocks}×${w.reps} · ${fmtClock(w.total_s)}`;
    $("tlTotal").textContent = w.total_s ? `total ${fmtClock(w.total_s)}` : "";
    drawTimeline(timeline, lastElapsed);
    return w;
  } catch (e) {
    alert(e.message);
  }
}

// ---------- controls ----------
let wakeLock = null;
async function keepAwake() {
  const want = document.body.classList.contains("riding") || rowerReady;
  try {
    if (want && !wakeLock && "wakeLock" in navigator) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    } else if (!want && wakeLock) {
      const lock = wakeLock;
      wakeLock = null;
      await lock.release();
    }
  } catch (_) { wakeLock = null; }
}
setInterval(keepAwake, 2000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });

function updateButtons() {
  const running = status === "running", paused = status === "paused";
  const riding = running || paused || countdownActive;
  if (document.body.classList.contains("riding") !== riding) {
    document.body.classList.toggle("riding", riding);
    window.dispatchEvent(new Event("resize"));
  }
  // the position slider places the boat only while it is not rowing
  const posSlider = $("posSlider");
  posSlider.disabled = riding;
  $("posHint").textContent = riding ? "(während der Fahrt gesperrt)" : "";
  if (riding && world) world.setPreview(null);
  $("btnStart").disabled = running || paused || countdownActive || !rowerReady;
  $("btnPause").disabled = !(running || paused);
  $("btnPause").textContent = paused ? "Resume" : "Pause";
  $("btnStop").disabled = !(running || paused);
}

$("btnLoad").onclick = loadWorkout;

// position slider: a spring back to the middle. Drag it up to 1 km back or forward: the boat moves along
// the river while you drag, and on release the slider returns to the middle. Only while the boat is not rowing.
// The readout shows the absolute position along the river (km from the start). While dragging it shows the
// place the boat is moving to; otherwise the boat's current place.
const fmtAbs = (m) => `${(Math.max(0, m) / 1000).toFixed(2).replace(".", ",")} km`;
let dragAnchor = null;   // the boat's absolute position when the drag began
$("posSlider").oninput = () => {
  const jump = +$("posSlider").value;
  if (!world) return;
  if (dragAnchor == null) dragAnchor = world.position();
  const target = Math.max(0, dragAnchor + jump);
  $("posVal").textContent = fmtAbs(target);
  world.setPreview(target);   // the boat follows the drag
};
$("posSlider").onchange = () => {
  const jump = +$("posSlider").value;
  if (world && dragAnchor != null) world.setPreview(Math.max(0, dragAnchor + jump));   // the final place
  dragAnchor = null;
  $("posSlider").value = 0;   // back to the middle
};
// the readout follows the boat when not dragging
setInterval(() => {
  if (world && dragAnchor == null) $("posVal").textContent = fmtAbs(world.position());
}, 200);
// choosing a preset fills the fields and loads it straight away
$("wPreset").onchange = async () => {
  const p = PRESETS[$("wPreset").value];
  if (!p) return;
  setWorkoutFields({ ...p, skip_last_recover: p.skip });
  await loadWorkout();
};

// Start checks the rower is live first; the sensor dialog opens if it is not.
async function startRow(skipCheck) {
  unlockAudio();
  if (status !== "running" && status !== "paused") {
    if (!(await loadWorkout())) return;
  }
  if (status !== "paused") {
    if (countdownActive) return;
    world?.holdAtStart?.(true);                     // the view goes to the start position as the countdown begins
    try { await runCountdown(10); } catch (_) { world?.holdAtStart?.(false); return; }
  }
  try {
    await api(status === "paused" ? "/api/row/resume" : "/api/row/start", {});
    status = "running";
  } catch (e) { alert(e.message); }
  world?.holdAtStart?.(false);
  countdownActive = false;
  updateButtons();
}

function runCountdown(from) {
  return new Promise((resolve, reject) => {
    countdownActive = true;
    updateButtons();
    const box = document.createElement("div");
    box.style.cssText = "position:fixed;inset:0;z-index:1000;display:flex;flex-direction:column;pointer-events:none;" +
      "align-items:center;justify-content:center;background:rgba(0,0,0,.35);color:#fff;";
    box.innerHTML = `<div style="font:700 180px/1 system-ui,sans-serif" id="cdNum"></div>` +
      `<button id="cdCancel" style="font-size:18px;margin-top:24px;padding:6px 18px;pointer-events:auto">Cancel</button>`;
    document.body.appendChild(box);
    let n = from;
    const show = () => { box.querySelector("#cdNum").textContent = n; beepCountdown(); };
    const finish = () => { clearInterval(timer); box.remove(); };
    show();
    const timer = setInterval(() => {
      n -= 1;
      if (n < 1) { beepPhaseChange(); finish(); resolve(); return; }
      show();
    }, 1000);
    box.querySelector("#cdCancel").onclick = () => {
      finish();
      countdownActive = false;
      updateButtons();
      reject(new Error("cancelled"));
    };
  });
}

$("btnStart").onclick = () => startRow(false);
$("btnSensors").onclick = () => openSensorDialog();
$("btnView").onclick = () => world?.resetView();
$("btnPause").onclick = async () => {
  try { await api(status === "paused" ? "/api/row/resume" : "/api/row/pause", {}); }
  catch (e) { alert(e.message); }
};
$("btnStop").onclick = async () => {
  if (!confirm("Stop the session and save the recording?")) return;
  try { await api("/api/row/stop", {}); } catch (e) { alert(e.message); }
};

// dolphins: "show dolphins" is on by default and remembered in this browser
const botsBox = $("botsOn");
try { if (localStorage.getItem("rowBotsOn") === "0") botsBox.checked = false; } catch (_) { /* no storage */ }
function applyBots() {
  world?.setBots?.(botsBox.checked);
  api("/api/row/bots", { on: botsBox.checked }).catch(() => {});
  try { localStorage.setItem("rowBotsOn", botsBox.checked ? "1" : "0"); } catch (_) { /* no storage */ }
}
botsBox.onchange = applyBots;

// tank fill setting: scales the displayed power (the speed is not affected)
function showFill(j) {
  $("fillL").value = j.litres;
  $("fillInfo").textContent = `power x${j.factor}`;
}
$("btnFill").onclick = async () => {
  try { showFill(await api("/api/row/fill", { litres: +$("fillL").value })); }
  catch (e) { alert(e.message); }
};
api_get_fill().catch(() => {});
async function api_get_fill() {
  const r = await fetch("/api/row/fill");
  if (r.ok) showFill(await r.json());
}

// ---------- live stream ----------
function onSnapshot(s) {
  status = s.status;
  lastElapsed = s.elapsed_s || 0;
  world?.setSnapshot(s);
  updateButtons();
  renderSnapshot(s);
  drawTimeline(timeline, lastElapsed);
  if ($("audioOn").checked && s.status === "running") {
    const key = `${s.phase}-${s.block}-${s.rep}`;
    if (lastPhaseKey !== null && key !== lastPhaseKey) beepPhaseChange();
    lastPhaseKey = key;
    if (s.phase_left_s > 0 && s.phase_left_s <= 3.5 && s.next_phase) {
      const c = Math.ceil(s.phase_left_s);
      if (c !== lastCountdown) { beepCountdown(); lastCountdown = c; }
    } else {
      lastCountdown = null;
    }
  }
  if (s.status === "finished" || s.status === "idle") lastPhaseKey = null;
}

const lastSeen = {}, lastValue = {};
// The marker's own time on this page's clock: its timestamp, corrected for the delivery delay.
// Lines arrive in bursts, so the arrival time alone is late and uneven.
let lagBase = Infinity;
function eventPerfMs(ts) {
  const delay = Date.now() / 1000 - ts;          // seconds from the line being read to this page
  lagBase = Math.min(lagBase, delay);
  return performance.now() - (delay - lagBase) * 1000;
}
function onSample(m) {
  if (m.metric === "stroke") world?.strokeEvent("ss", eventPerfMs(m.ts));
  else if (m.metric === "stroke_end") world?.strokeEvent("se", eventPerfMs(m.ts));
  lastSeen[m.metric] = Date.now();
  lastValue[m.metric] = m.value;
  const age = (k) => (lastSeen[k] ? (Date.now() - lastSeen[k]) / 1000 : null);
  setSensorChip("chipHr", `HR ${lastValue.heart_rate != null ? Math.round(lastValue.heart_rate) : "-"}`, age("heart_rate"));
  setSensorChip("chipSpm", `stroke ${lastValue.stroke_rate != null ? Math.round(lastValue.stroke_rate) : "-"}`, age("stroke_rate"));
  setSensorChip("chipSpd", `speed ${lastValue.speed != null ? lastValue.speed.toFixed(2) : "-"}`, age("speed"));
}

function connectStream() {
  const es = new EventSource("/api/row/stream");
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "row") onSnapshot(m);
    else if (m.type === "sample") onSample(m);
  };
  es.onerror = () => { $("viewMsg").textContent = "Stream lost - retrying..."; $("viewMsg").hidden = false; };
  es.onopen = () => { $("viewMsg").hidden = true; };
}

async function init() {
  try {
    world = createRowWorld($("view"), (fps) => { $("fps").textContent = fps + " fps"; });
  } catch (e) {
    $("viewMsg").textContent = "3D view unavailable: " + e.message;
  }
  wireSensors();
  try {
    const w = await (await fetch("/api/row/workout")).json();
    timeline = setTimeline(w);
    setWorkoutFields({ ...w, skip_last_recover: w.skip_last_recover, targets: w.targets });
    $("loadInfo").textContent = `${w.blocks}×${w.reps} · ${fmtClock(w.total_s)}`;
    drawTimeline(timeline, 0);
  } catch (e) { /* server not ready */ }
  applyBots();
  onSnapshot(await (await fetch("/api/row/state")).json().catch(() => ({ status: "none" })));
  connectStream();
  const poll = async () => {
    const st = await refreshSensors();
    // ready as soon as the port is open (or the simulator runs): the data starts with the first stroke
    rowerReady = !!(st && (st.connected || sensorsLive(st)));
    updateButtons();
  };
  await poll();
  setInterval(poll, 2000);
  window.addEventListener("resize", () => drawTimeline(timeline, lastElapsed));
  $("viewMsg").hidden = !!world;
}

init().catch((e) => { $("viewMsg").textContent = "Failed to start: " + e.message; });
