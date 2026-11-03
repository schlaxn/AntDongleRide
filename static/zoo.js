// Animal viewer: one animal on a ground disc, with the same camera controls as the apps.
// URL options (for screenshots): animal, coat, state, t (freeze: stride phase for walk, seconds
// for the others), cam (three|side|front|top), zoom (m), speed, ui=0 (hide the panel).
import * as THREE from "/static/vendor/three.module.min.js";
import { COATS, createCat } from "/static/shared/animals/cat.js";
import { SPRINT, STATES, WALK, groom, lerpPose, sit, sprint, sprintSpeed, stand, stretch, walk, walkSpeed } from "/static/shared/animals/gaits.js";
import { smooth01 } from "/static/shared/animals/rig.js";

const $ = (id) => document.getElementById(id);
const Q = new URLSearchParams(location.search);
const qNum = (k) => (Q.has(k) && isFinite(parseFloat(Q.get(k))) ? parseFloat(Q.get(k)) : null);

// ---------- scene ----------
const view = $("view");
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setClearColor(0x9fd8f4);
view.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9fd8f4);
const camera = new THREE.PerspectiveCamera(45, 1, 0.05, 200);
const sun = new THREE.DirectionalLight(0xfff1d6, 1.6);
sun.position.set(-3, 6, 4);
scene.add(sun, new THREE.HemisphereLight(0xcfe8ff, 0x5a6b3a, 0.9));

// the ground: a striped texture, so a paw that slides on it shows
const TILE = 0.5;
const gc = document.createElement("canvas");
gc.width = gc.height = 256;
const gg = gc.getContext("2d");
gg.fillStyle = "#8fc468"; gg.fillRect(0, 0, 256, 256);
gg.fillStyle = "#7fb55a"; gg.fillRect(0, 0, 256, 128);
gg.fillStyle = "#6ea34c"; gg.fillRect(0, 0, 8, 256);
const groundTex = new THREE.CanvasTexture(gc);
groundTex.wrapS = groundTex.wrapT = THREE.RepeatWrapping;
groundTex.repeat.set(40, 40);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(TILE * 40, TILE * 40), new THREE.MeshLambertMaterial({ map: groundTex }));
ground.rotation.x = -Math.PI / 2;
scene.add(ground);

// ---------- the animal ----------
let cat = null;
function makeAnimal(coat) {
  if (cat) scene.remove(cat.group);
  cat = createCat({ coat });
  scene.add(cat.group);
}
makeAnimal(Q.get("coat") || "tabby");
for (const name of Object.keys(COATS)) {
  const o = document.createElement("option");
  o.value = o.textContent = name;
  $("coat").appendChild(o);
}
$("coat").value = Q.get("coat") || "tabby";
$("coat").onchange = () => makeAnimal($("coat").value);

// ---------- states ----------
const frozenT = qNum("t");
let state = STATES.includes(Q.get("state")) ? Q.get("state") : "stand";
let stateT = frozenT != null ? frozenT : 0, walkPhase = frozenT != null ? frozenT : 0;
let from = null, blendT = 1, lastPose = null;
let paused = frozenT != null, treadmill = true, rate = qNum("speed") || 1;

function targetPose() {
  switch (state) {
    case "walk": return walk(walkPhase, stateT);
    case "sprint": return sprint(walkPhase, stateT);
    case "sit": return sit(stateT);
    case "groom": return groom(stateT);
    case "stretch": return stretch(stateT);
    default: return stand(stateT);
  }
}
function setState(s) {
  if (s === state) return;
  from = lastPose;
  blendT = 0;
  state = s;
  stateT = 0;
  for (const b of document.querySelectorAll("#states button")) b.classList.toggle("on", b.dataset.state === s);
}
for (const s of STATES) {
  const b = document.createElement("button");
  b.textContent = s;
  b.dataset.state = s;
  b.onclick = () => setState(s);
  $("states").appendChild(b);
}
for (const b of document.querySelectorAll("#states button")) b.classList.toggle("on", b.dataset.state === state);
$("speed").value = rate;
$("speedVal").textContent = rate.toFixed(1) + "x";
$("speed").oninput = () => { rate = parseFloat($("speed").value); $("speedVal").textContent = rate.toFixed(1) + "x"; };
$("pause").onclick = () => { paused = !paused; $("pause").textContent = paused ? "Play" : "Pause"; };
$("pause").textContent = paused ? "Play" : "Pause";
$("treadmill").onclick = () => { treadmill = !treadmill; $("treadmill").classList.toggle("on", treadmill); };
if (Q.get("ui") === "0") { $("panel").style.display = "none"; $("hint").style.display = "none"; $("fps").style.display = "none"; }

// ---------- camera, the same controls as the apps ----------
// drag: orbit; right-drag or shift-drag: pan; wheel: zoom; double-click: reset;
// arrows: orbit, +/-: zoom, WASD: pan, R: reset, C: turntable (slow automatic orbit).
const VIEWS = {
  three: { yaw: 0.9, pitch: 0.3, dist: 1.5 },
  side: { yaw: Math.PI / 2, pitch: 0.08, dist: 1.4 },
  front: { yaw: Math.PI, pitch: 0.12, dist: 1.4 },
  top: { yaw: 0, pitch: 1.45, dist: 1.6 },
};
const DEFAULT_NAV = VIEWS.three;
const TARGET = new THREE.Vector3(0, 0.15, 0);
const nav = { ...DEFAULT_NAV, pan: { x: 0, y: 0 }, turntable: false };
const drag = { active: false, button: 0, x: 0, y: 0 };
const keys = new Set();
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const el = renderer.domElement;
el.style.touchAction = "none";
function setView(name) {
  Object.assign(nav, VIEWS[name], { pan: { x: 0, y: 0 }, turntable: false });
}
function resetNav() { Object.assign(nav, DEFAULT_NAV, { pan: { x: 0, y: 0 }, turntable: false }); }
if (VIEWS[Q.get("cam")]) setView(Q.get("cam"));
if (qNum("zoom") != null) nav.dist = qNum("zoom");
for (const b of document.querySelectorAll("button[data-cam]")) b.onclick = () => setView(b.dataset.cam);

function updateCamera(dt) {
  const turn = 1.6 * dt, zoom = 1.2 * dt, pan = 0.8 * dt * Math.max(0.5, nav.dist);
  if (nav.turntable) nav.yaw += 0.35 * dt;
  if (keys.has("ArrowLeft")) nav.yaw -= turn;
  if (keys.has("ArrowRight")) nav.yaw += turn;
  if (keys.has("ArrowUp")) nav.pitch = clamp(nav.pitch + turn * 0.6, -0.1, 1.5);
  if (keys.has("ArrowDown")) nav.pitch = clamp(nav.pitch - turn * 0.6, -0.1, 1.5);
  if (keys.has("=") || keys.has("+")) nav.dist = clamp(nav.dist * Math.exp(-zoom), 0.4, 30);
  if (keys.has("-")) nav.dist = clamp(nav.dist * Math.exp(zoom), 0.4, 30);
  if (keys.has("a")) nav.pan.x -= pan;
  if (keys.has("d")) nav.pan.x += pan;
  if (keys.has("w")) nav.pan.y += pan;
  if (keys.has("s")) nav.pan.y -= pan;
  const cp = Math.cos(nav.pitch);
  const off = new THREE.Vector3(Math.sin(nav.yaw) * cp, Math.sin(nav.pitch), Math.cos(nav.yaw) * cp).multiplyScalar(nav.dist);
  const look = new THREE.Vector3(TARGET.x + nav.pan.x, TARGET.y + nav.pan.y, TARGET.z);
  camera.position.copy(look).add(off);
  camera.lookAt(look);
}
el.addEventListener("pointerdown", (e) => {
  drag.active = true; drag.button = e.button; drag.x = e.clientX; drag.y = e.clientY;
  nav.turntable = false;
  el.setPointerCapture(e.pointerId);
});
el.addEventListener("pointermove", (e) => {
  if (!drag.active) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  drag.x = e.clientX; drag.y = e.clientY;
  if (drag.button === 2 || e.shiftKey) {
    nav.pan.x -= dx * 0.0015 * nav.dist;
    nav.pan.y += dy * 0.0015 * nav.dist;
  } else {
    nav.yaw -= dx * 0.006;
    nav.pitch = clamp(nav.pitch + dy * 0.004, -0.1, 1.5);
  }
});
const endDrag = () => { drag.active = false; };
el.addEventListener("pointerup", endDrag);
el.addEventListener("pointercancel", endDrag);
el.addEventListener("wheel", (e) => {
  e.preventDefault();
  nav.dist = clamp(nav.dist * Math.exp(e.deltaY * 0.001), 0.4, 30);
}, { passive: false });
el.addEventListener("dblclick", resetNav);
el.addEventListener("contextmenu", (e) => e.preventDefault());
window.addEventListener("keydown", (e) => {
  if (/INPUT|SELECT|TEXTAREA|BUTTON/.test(e.target.tagName)) return;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (k === "r") resetNav();
  else if (k === "c") nav.turntable = !nav.turntable;
  else keys.add(k);
  if (k.startsWith("Arrow")) e.preventDefault();
});
window.addEventListener("keyup", (e) => keys.delete(e.key.length === 1 ? e.key.toLowerCase() : e.key));

// ---------- loop ----------
function fit() {
  const w = Math.max(1, view.clientWidth), h = Math.max(1, view.clientHeight);
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(fit).observe(view);
fit();

let last = performance.now(), frames = 0, fpsT = last;
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  if (!paused) {
    stateT += dt * rate;
    walkPhase = (walkPhase + (dt * rate) / (state === "sprint" ? SPRINT.period : WALK.period)) % 1;
    blendT += dt / 0.4;
    if ((state === "walk" || state === "sprint") && treadmill) groundTex.offset.y += ((state === "sprint" ? sprintSpeed(1) : walkSpeed(1)) * dt * rate) / TILE;
  }
  let pose = targetPose();
  if (from && blendT < 1) pose = lerpPose(from, pose, smooth01(blendT));
  lastPose = pose;
  cat.apply(pose);
  updateCamera(dt);
  renderer.render(scene, camera);
  window.__rowFrames = (window.__rowFrames || 0) + 1;      // lets tools/snap.py wait for real frames
  frames++;
  if (now - fpsT >= 1000) { $("fps").textContent = Math.round((frames * 1000) / (now - fpsT)) + " fps"; frames = 0; fpsT = now; }
}
requestAnimationFrame(frame);
