// Ride page controller: talks to the Flask API, consumes the SSE stream and drives the HUD,
// the timeline, audio cues and (later) the 3D view.
import { beepCountdown, beepPhaseChange, unlockAudio } from "./audio.js";
import { drawTimeline, fmtClock, renderSnapshot, setSensorChip, setTimeline } from "./hud.js";
import { createWorld } from "./world.js";
import { missingRoles, openSensorDialog } from "./sensors.js";

const $ = (id) => document.getElementById(id);
let world = null;
const api = async (url, body) => {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(url, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
};

let status = "none";
let lastPhaseKey = null;
let lastCountdown = null;
const lastSeen = {}; // metric -> ms timestamp of last sample

// ---------- workout + settings ----------
const PRESETS = {
  quick: { blocks: 1, reps: 3, on_s: 20, off_s: 10, between_s: 0, warmup_s: 15, cooldown_s: 15, skip: true },
  std: { blocks: 3, reps: 13, on_s: 30, off_s: 15, between_s: 300, warmup_s: 600, cooldown_s: 600, skip: true },
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
    ghost: Object.fromEntries(GHOST_KINDS.map((k) => [k, { speed_kmh: +$(`gh_${k}_v`).value }])),
    gradients: {
      warmup: +$("gWarm").value, work: +$("gWork").value, recover: +$("gRec").value,
      between: +$("gBetween").value, cooldown: +$("gCool").value,
    },
  };
}

async function loadWorkout() {
  try {
    await api("/api/config", { wheel_circumference_m: +$("wheel").value, gradients: workoutBody().gradients });
    const w = await api("/api/workout", workoutBody());
    setTimeline(w);
    world?.setWorkout(w);
    $("loadInfo").textContent = `${w.shorthand} · ${fmtClock(w.total_s)}`;
    return w;
  } catch (e) {
    alert(e.message);
  }
}

async function loadSettings() {
  const cfg = await (await fetch("/api/config")).json();
  $("wheel").value = cfg.wheel_circumference_m;
  $("gWarm").value = cfg.gradients.warmup;
  $("gWork").value = cfg.gradients.work;
  $("gRec").value = cfg.gradients.recover;
  $("gBetween").value = cfg.gradients.between;
  $("gCool").value = cfg.gradients.cooldown;
}

// ---------- controls ----------
// Start is only enabled once the speed sensor is live (heart rate is optional).
let speedReady = false;
async function refreshSpeedReady() {
  const s = await fetch("/api/sensors").then((r) => r.json()).catch(() => null);
  const ready = !!(s && s.roles && s.roles.speed && s.roles.speed.live);
  if (ready !== speedReady) { speedReady = ready; updateButtons(); }
}
setInterval(refreshSpeedReady, 2000);
function updateButtons() {
  const running = status === "running", paused = status === "paused";
  // full-screen riding layout while a ride is running or paused; back to setup when it ends
  const riding = running || paused || countdownActive;
  if (document.body.classList.contains("riding") !== riding) {
    document.body.classList.toggle("riding", riding);
    window.dispatchEvent(new Event("resize"));   // timeline redraws at the new width
  }
  $("btnStart").disabled = running || paused || countdownActive || !speedReady;
  $("btnPause").disabled = !(running || paused);
  $("btnPause").textContent = paused ? "Resume" : "Pause";
  $("btnStop").disabled = !(running || paused);
}

$("btnLoad").onclick = loadWorkout;
// choosing a preset fills the workout fields straight away
$("wPreset").onchange = () => {
  const p = PRESETS[$("wPreset").value];
  if (p) setWorkoutFields(p);
  if (p) {
    // choosing a preset also shows the ghost rider
    $("ghostOn").checked = true;
    applyGhostVisibility();
    world?.setGhost(true);
  }
};
// Start checks the required sensors first and opens the sensor dialog if one is missing.
async function startRide(skipCheck) {
  unlockAudio();
  if (!skipCheck && status !== "paused") {
    const missing = await missingRoles().catch(() => []);
    if (missing.length) { openSensorDialog(() => startRide(true)); return; }
  }
  if (status !== "running" && status !== "paused") {
    if (!(await loadWorkout())) return;
  }
  if (status !== "paused") {
    // a new ride always shows the ghost rider in the left lane
    $("ghostOn").checked = true;
    applyGhostVisibility();
    world?.setGhost(true);
  }
  if (status !== "paused") {
    // 10-0 countdown before a new ride starts; Cancel aborts it
    if (countdownActive) return;
    try { await runCountdown(10); } catch (_) { return; }
  }
  try {
    await api(status === "paused" ? "/api/ride/resume" : "/api/ride/start", {});
    // the server has started the ride: switch to running now, not at the next stream update
    // (that gap is what made the layout flicker back to setup)
    status = "running";
  } catch (e) { alert(e.message); }
  countdownActive = false;
  updateButtons();
}
let countdownActive = false;
function runCountdown(from) {
  return new Promise((resolve, reject) => {
    countdownActive = true;
    updateButtons();   // full-screen riding layout starts with the countdown
    const box = document.createElement("div");
    // the overlay lets mouse and keyboard through to the 3D view (camera control); only Cancel takes clicks
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
      if (n < 1) {                 // no "0" on screen: the ride starts on the final beep
        beepPhaseChange();
        finish();
        resolve();
        return;
      }
      show();
    }, 1000);
    box.querySelector("#cdCancel").onclick = () => {
      finish();
      countdownActive = false;   // cancelled: back to the setup layout
      updateButtons();
      reject(new Error("cancelled"));
    };
  });
}
$("btnStart").onclick = () => startRide(false);
$("btnSensors").onclick = () => openSensorDialog(() => startRide(true));
$("btnView").onclick = () => world?.resetView();

// ghost rider: per interval, speed (km/h) and power (W) are linked by the trainer power curve
const GHOST_KINDS = ["warmup", "work", "recover", "between", "cooldown"];
const PM_EXP = Math.log(540 / 145) / Math.log(40 / 20);   // same curve as the server (145 W @ 20, 540 W @ 40)
const powerFor = (v) => 145 * Math.pow(v / 20, PM_EXP);
const speedFor = (p) => 20 * Math.pow(p / 145, 1 / PM_EXP);
for (const k of GHOST_KINDS) {
  $(`gh_${k}_v`).onchange = () => { $(`gh_${k}_p`).value = Math.round(powerFor(+$(`gh_${k}_v`).value)); };
  $(`gh_${k}_p`).onchange = () => { $(`gh_${k}_v`).value = speedFor(+$(`gh_${k}_p`).value).toFixed(1); };
}
// the ghost parameters only show while the ghost is on
function applyGhostVisibility() {
  const on = $("ghostOn").checked;
  $("ivtable").classList.toggle("ghost-on", on);
  $("ghnote").hidden = !on;
}
$("ghostOn").onchange = () => { world?.setGhost($("ghostOn").checked); applyGhostVisibility(); };
$("ghostMode").onchange = () => world?.setGhostMode($("ghostMode").value);
applyGhostVisibility();
// one column at a time: km/h or W, switched by the button; both values stay in sync
let ghostMode = "speed";
function applyGhostMode() {
  for (const k of GHOST_KINDS) {
    $(`gh_${k}_v`).hidden = ghostMode !== "speed";
    $(`gh_${k}_p`).hidden = ghostMode !== "power";
  }
  $("btnGhostMode").textContent = ghostMode === "speed" ? "Show W" : "Show km/h";
}
$("btnGhostMode").onclick = () => { ghostMode = ghostMode === "speed" ? "power" : "speed"; applyGhostMode(); };
applyGhostMode();
function setGhostFields(ghost) {
  for (const k of GHOST_KINDS) {
    $(`gh_${k}_v`).value = ghost[k].speed_kmh;
    $(`gh_${k}_p`).value = Math.round(ghost[k].power_w);
  }
}

// testing: switch the speed used by the ride between x1 and x10
let speedFactor = 1;
async function refreshSpeedFactor() {
  try {
    speedFactor = (await (await fetch("/api/test/speed-multiplier")).json()).factor;
  } catch (_) { /* keep the last value */ }
  $("btnSpeedX").textContent = `Speed ×${speedFactor}`;
}
$("btnSpeedX").onclick = async () => {
  const next = speedFactor === 1 ? 5 : 1;   // toggles between x1 and x5
  try { await api("/api/test/speed-multiplier", { factor: next }); } catch (e) { alert(e.message); }
  refreshSpeedFactor();
};
$("btnPause").onclick = async () => {
  try { await api(status === "paused" ? "/api/ride/resume" : "/api/ride/pause", {}); }
  catch (e) { alert(e.message); }
};
$("btnStop").onclick = async () => {
  if (!confirm("Stop the ride and save the recording?")) return;
  try { await api("/api/ride/stop", {}); } catch (e) { alert(e.message); }
};

// ---------- live stream ----------
let lastElapsed = 0;

function onSnapshot(s) {
  status = s.status;
  lastElapsed = s.elapsed_s || 0;
  world?.setSnapshot(s);
  updateButtons();
  renderSnapshot(s);
  drawTimeline(lastElapsed);
  if ($("audioOn").checked && (s.status === "running")) {
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
  if (s.status === "finished") lastPhaseKey = null;
  if (s.status === "idle") lastPhaseKey = null;
}

const lastValue = {};
function onSample(m) {
  lastSeen[m.metric] = Date.now();
  lastValue[m.metric] = m.value;
  const age = (k) => (lastSeen[k] ? (Date.now() - lastSeen[k]) / 1000 : null);
  setSensorChip("chipHr", `HR ${lastValue.heart_rate != null ? Math.round(lastValue.heart_rate) : "-"}`, age("heart_rate"));
  setSensorChip("chipCad", `cadence ${lastValue.cadence != null ? Math.round(lastValue.cadence) : "-"}`, age("cadence"));
  setSensorChip("chipSpd", `speed ${lastValue.speed != null ? lastValue.speed.toFixed(1) : "-"}`, age("speed"));
  const anyAge = Math.min(...["heart_rate", "cadence", "speed"].map((k) => (lastSeen[k] ? Date.now() - lastSeen[k] : Infinity)));
  $("sensorPill").textContent = anyAge < 3000 ? "sensors live" : "no sensors";
  $("sensorPill").className = "pill " + (anyAge < 3000 ? "ok" : "");
}

function connectStream() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "ride") onSnapshot(m);
    else if (m.type === "sample") onSample(m);
  };
  es.onerror = () => { $("viewMsg").textContent = "Stream lost - retrying..."; };
}

async function init() {
  try {
    world = createWorld($("view"), (fps) => { $("fps").textContent = fps + " fps"; });
  } catch (e) {
    $("viewMsg").textContent = "3D view unavailable: " + e.message;
  }
  await loadSettings();
  const w = await (await fetch("/api/workout")).json();
  setTimeline(w);
  world?.setWorkout(w);
  setWorkoutFields(w);
  if (w.ghost) setGhostFields(w.ghost);   // absent if the server is older than the ghost feature
  world?.setGhost($("ghostOn").checked);
  $("loadInfo").textContent = w.loaded ? `${w.shorthand} · ${fmtClock(w.total_s)}` : "";
  onSnapshot(await (await fetch("/api/ride/state")).json());
  if (world) $("viewMsg").hidden = true;
  connectStream();
  window.addEventListener("resize", () => drawTimeline(lastElapsed));
}

init().catch((e) => { $("viewMsg").textContent = "Failed to start: " + e.message; });
