// Low-poly 3D ride world.
//
// The world is rider-relative: the rider stays at the origin and the road is rebuilt every frame
// for distances -40 m .. +300 m around them. Each road row at distance d gets the gradient the
// workout prescribes at the time the rider will reach it: t = now + d / v. Heights are integrated
// from those gradients, so the road is always continuous and the slope ahead morphs with speed.
//
// Smoothness notes:
//  - the gradient is interpolated between seconds (no steps in the slope)
//  - road markings are a texture on an overlay mesh whose UVs scroll continuously with distance,
//    so dashes glide instead of snapping from row to row
import * as THREE from "/static/vendor/three.module.min.js";

const D_BACK = 40, D_AHEAD = 300;
const ROWS_D = [];
for (let d = -D_BACK; d < 60; d += 1) ROWS_D.push(d);        // 1 m rows close to the rider
for (let d = 60; d <= D_AHEAD; d += 2.5) ROWS_D.push(d);     // 2.5 m rows further out
const ROWS = ROWS_D.length;
const R0 = D_BACK;                                           // index of d = 0
const rowD = Float64Array.from(ROWS_D);
const SEG = new Float64Array(ROWS - 1);
for (let k = 0; k < ROWS - 1; k++) SEG[k] = rowD[k + 1] - rowD[k];

const XS = [-80, -40, -20, -10, -6, -4.3, -3.5, -3.2, -3.0, -1.75, -0.12, 0, 0.12, 1.75, 3.0, 3.2, 3.5, 4.3, 6, 10, 20, 40, 80];
const COLS = XS.length;
const OX = [-4.3, -2.15, 0, 2.15, 4.3];                      // overlay columns (road only)
const ROAD_HALF = 3.5;
const DASH_PERIOD = 9;                                       // m: 3 m dash, 6 m gap
const MIN_V = 5.5;
const WHEEL_R = 0.34;
// lanes: the road is 7 m wide with the centre line at x = 0 and edge lines at ±3.5; the camera looks down -z, so right is +x
const LANE_X = 2.5;                                          // rider: 1 m in from the right-hand edge line
const WHEEL_CIRC = 2.105;   // m, same default as the server
const VIRTUAL_GEAR = 2.6;   // wheel revolutions per crank revolution, used when there is no cadence sensor
const CRANK = 0.17;
const ROCK_RAD = 0.0122;   // side-to-side "Kippbewegung" while pedalling: 0.7 degrees (0 turns it off)
const UP = new THREE.Vector3(0, 1, 0);
const FWD = new THREE.Vector3(0, 0, 1);
const V = THREE.Vector3;

// sun direction (from the scene towards the sun): high, in front and to the left
const SUN_DIR = new V(-30, 60, -20).normalize();
const SKY_TOP = new THREE.Color(0x3a7bd5), SKY_HORIZON = new THREE.Color(0xc3e0f5);

const hash = (n) => { const s = Math.sin(n * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s); };
const vnoise = (x) => { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return hash(i) * (1 - u) + hash(i + 1) * u; };

// metres above road level at lateral offset x and absolute distance dAbs
function groundOffset(x, dAbs) {
  const side = Math.min(1, Math.max(0, Math.abs(x) - ROAD_HALF - 1.5) / 10);
  const h = (vnoise(dAbs * 0.012 + x * 0.03) - 0.5) * 16 + (vnoise(dAbs * 0.05 + 7.1) - 0.5) * 3;
  return h * side;
}

// [index k, fraction f] such that rowD[k] + f * SEG[k] = d
function rowIndexFor(d) {
  if (d <= rowD[0]) return [0, 0];
  if (d >= rowD[ROWS - 1]) return [ROWS - 2, 1];
  let lo = 0, hi = ROWS - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (rowD[mid] <= d) lo = mid; else hi = mid; }
  return [lo, (d - rowD[lo]) / SEG[lo]];
}

function gridIndex(rows, cols) {
  const idx = [];
  for (let r = 0; r < rows - 1; r++) {
    for (let c = 0; c < cols - 1; c++) {
      const a = r * cols + c, b = a + 1, cc = a + cols, d = cc + 1;
      idx.push(a, b, cc, b, d, cc); // counter-clockwise seen from above
    }
  }
  return idx;
}

// geometry helpers for limbs and frame tubes: a cylinder/box is stretched between two points
const _d = new V(), _m = new V();
function setTube(mesh, a, b, axis) {
  _d.subVectors(b, a);
  const len = Math.max(_d.length(), 1e-4);
  _m.addVectors(a, b).multiplyScalar(0.5);
  mesh.position.copy(_m);
  mesh.quaternion.setFromUnitVectors(axis, _d.divideScalar(len));
  if (axis === UP) mesh.scale.set(1, len, 1);
  else mesh.scale.set(1, 1, len);
}

const cylCache = {};
const cylGeo = (r) => cylCache[r] || (cylCache[r] = new THREE.CylinderGeometry(r, r, 1, 8));
const boxCache = {};
const boxGeo = (w, h) => {
  const key = w + "x" + h;
  return boxCache[key] || (boxCache[key] = new THREE.BoxGeometry(w, h, 1));
};

// inverse kinematics for a two-bone limb: returns the middle joint (knee or elbow)
function ikJoint(root, end, L1, L2, bendHint) {
  const u = new V().subVectors(end, root);
  let d = u.length();
  if (d < 1e-4) return root.clone();
  d = Math.min(d, L1 + L2 - 1e-3);
  u.normalize();
  const a = (d * d + L1 * L1 - L2 * L2) / (2 * d);
  const h = Math.sqrt(Math.max(0, L1 * L1 - a * a));
  const v = bendHint.clone().addScaledVector(u, -bendHint.dot(u)).normalize();
  return root.clone().addScaledVector(u, a).addScaledVector(v, h);
}

// ---------- bike geometry (bike-local coordinates, origin = wheel centre height) ----------
// Orbea Orca from the owner's measurements: 57 cm frame, 73/73 deg head and seat tube,
// 58 cm effective top tube (slight slope), 810 mm saddle height from BB, 170 mm cranks, 28" wheels,
// 100 mm stem, 75 mm reach, 125 mm drop. Chainstay (405 mm) and wheelbase (1010 mm) are estimates.
const BB = new V(0, -0.07, 0);
const REAR = new V(0, 0, 0.405), FRONT = new V(0, 0, -0.605);
const ST_TOP = new V(0, 0.475, 0.166);
const SADDLE_TOP = new V(0, 0.705, 0.236);                  // 0.81 m along the seat tube from the BB
const HEAD_TOP = new V(0, 0.49, -0.38), HEAD_BOT = new V(0, 0.356, -0.421);   // head tube leans back 73 deg
const TOP_START = ST_TOP.clone();
const TOP_END = new V(0, 0.515, -0.40);                     // top tube rises towards the head tube
const TT_HT = TOP_END;
const STEM_C = new V(0, TT_HT.y + 0.05, TT_HT.z);
const STEM_V = new V(0, 0.1 * Math.sin(12 * Math.PI / 180), -0.1 * Math.cos(12 * Math.PI / 180));
const HEAD_DIR = new V(0, Math.sin(73 * Math.PI / 180), Math.cos(73 * Math.PI / 180));
// head tube top and the spacers both run along the head axis (73 deg): the joint is 4 cm up the axis,
// the stem base sits on the same axis, so the spacers are 4 cm shorter than before
const HEAD_JOINT = HEAD_TOP.clone().addScaledVector(HEAD_DIR, 0.04);
const STEM_BOT = HEAD_TOP.clone().addScaledVector(HEAD_DIR, 0.105);
const STEM_TOP = STEM_BOT.clone().add(STEM_V);                        // bar clamp end
const BAR_CLAMP = STEM_TOP.clone();
// the whole handlebar assembly (bar, drops, hands) is tilted up by BAR_TILT about the stem clamp
const BAR_TILT = 3 * Math.PI / 180;
function tiltBar(p) {
  const dy = p.y - BAR_CLAMP.y, dz = p.z - BAR_CLAMP.z;   // forward is -z, so the front rises
  return new V(p.x,
    BAR_CLAMP.y + dy * Math.cos(BAR_TILT) - dz * Math.sin(BAR_TILT),
    BAR_CLAMP.z + dy * Math.sin(BAR_TILT) + dz * Math.cos(BAR_TILT));
}
const HAND_Z = -0.564;   // hand (STI) position, 2 cm forward of the original
const GRIPS = [tiltBar(new V(-0.20, 0.605, HAND_Z)), tiltBar(new V(0.20, 0.605, HAND_Z))];

// rider 184 cm, 91 cm inseam: hips sit on the saddle top; thigh + shin = inseam
const HIP_MID = new V(0, 0.745, 0.206);
const HIP_X = 0.1;
const THIGH = 0.46, SHIN = 0.45, UPPER_ARM = 0.32, FOREARM = 0.30;
const TORSO_DIR = new V(0, 0.64, -0.77).normalize();  // about 40 deg from horizontal (road position)
const TORSO_LEN = 0.55;

// Handlebar: a straight horizontal centre bar (20 cm, x -0.10..0.10) at height BAR_Y, then on each side
// a narrow 90-degree rounded corner (radius 0.10 m) that ends exactly at the STI hand position.
// The bar towards the STIs stays horizontal (constant y).
const BAR_Y = 0.605, BAR_Z = -0.434;   // untilted; tiltBar() is applied to the finished points. Reach 1 cm shorter.
function barPath(s) {
  const pts = [];
  for (let i = 0; i <= 12; i++) {
    const t = (i / 12) * (Math.PI / 2);
    pts.push(new V(s * (0.10 + 0.10 * Math.sin(t)), BAR_Y, BAR_Z - 0.10 + 0.10 * Math.cos(t)));
  }
  // drops, from the bar end (STI) on: parallel to the frame, then a narrow 105-degree bend down,
  // then a wide 60-degree arc, then a straight section pointing back towards the rear axle
  const DEG = Math.PI / 180;
  pts.push(new V(s * 0.20, BAR_Y, HAND_Z));                         // short straight run to the hand position (STI)
  const S = pts[pts.length - 1];                                    // STI
  const d0 = new V(0, 0, -1);                                       // forward, parallel to the frame
  const d1 = new V(0, -Math.sin(105 * DEG), -Math.cos(105 * DEG));  // 105 degrees down
  const d2 = new V(0, -Math.sin(165 * DEG), -Math.cos(165 * DEG));  // 60 degrees more, now pointing back
  const V1 = S.clone().addScaledVector(d0, 0.08);
  const V2 = V1.clone().addScaledVector(d1, 0.12);
  const V3 = V2.clone().addScaledVector(d2, 0.12);
  return [
    ...pts,
    ...roundCorner(V1, d0, d1, 0.02, 6),     // narrow 105-degree bend
    ...roundCorner(V2, d1, d2, 0.10, 10),    // wide 60-degree arc
    V3,                                      // straight section towards the rear axle
  ].map(tiltBar);
}
// Rounded corner at V0 between incoming direction u and outgoing direction w (unit vectors) with radius r.
// Returns sampled points from the tangent point before the corner to the one after it.
function roundCorner(V0, u, w, r, n) {
  const phi = Math.acos(Math.min(1, Math.max(-1, u.dot(w))));   // deflection angle
  const t = r * Math.tan(phi / 2);                                // distance from the corner to each tangent point
  const T1 = V0.clone().addScaledVector(u, -t);
  const T2 = V0.clone().addScaledVector(w, t);
  const C = V0.clone().addScaledVector(w.clone().sub(u).normalize(), r / Math.cos(phi / 2));   // arc centre
  const a0 = T1.clone().sub(C).normalize();
  const a1 = T2.clone().sub(C).normalize();
  const perp = a1.clone().addScaledVector(a0, -a1.dot(a0)).normalize();
  const out = [];
  for (let k = 0; k <= n; k++) {
    const a = (k / n) * phi;
    out.push(C.clone().addScaledVector(a0, r * Math.cos(a)).addScaledVector(perp, r * Math.sin(a)));
  }
  return out;
}
// straight pieces between the sampled corner points, so the tube follows the true corner
function barCurve(s) {
  const pts = barPath(s);
  const path = new THREE.CurvePath();
  for (let i = 1; i < pts.length; i++) path.add(new THREE.LineCurve3(pts[i - 1], pts[i]));
  return path;
}

// pedal position in bike coordinates; forward is -z, so at the top of the stroke the pedal moves forward
function pedalPos(side, phi) {
  const a = side > 0 ? phi : phi + Math.PI;
  return new V(side * 0.09, BB.y + CRANK * Math.cos(a), BB.z - CRANK * Math.sin(a));
}

// Builds one bike with its rider. `m` holds the materials; the ghost passes translucent ones.
// Returns the group (position it in the world), pose(crankAngle, pedalling) and setWheels(angle).
function buildRig(m, castShadow) {
  const bike = new THREE.Group();
  const frameTube = (r, a, b, mm = m.frame) => {
    const t = new THREE.Mesh(cylGeo(r), mm);
    setTube(t, a, b, UP);
    bike.add(t);
    return t;
  };
  frameTube(0.026, BB, HEAD_BOT);                                    // down tube
  frameTube(0.02, TOP_START, TOP_END);                               // top tube
  frameTube(0.022, BB, ST_TOP);                                      // seat tube
  frameTube(0.03, HEAD_BOT, HEAD_JOINT);                             // head tube
  for (const s of [-1, 1]) {
    frameTube(0.012, BB.clone().add(new V(s * 0.05, 0, 0)), REAR.clone().add(new V(s * 0.05, 0, 0)));                 // chainstays
    frameTube(0.011, ST_TOP.clone().add(new V(s * 0.04, -0.05, 0)), REAR.clone().add(new V(s * 0.04, 0, 0)));         // seatstays
    frameTube(0.013, HEAD_BOT.clone().add(new V(s * 0.05, 0, 0)), FRONT.clone().add(new V(s * 0.05, 0, 0)), m.black); // fork
  }
  frameTube(0.016, ST_TOP, SADDLE_TOP, m.metal);                     // seat post
  frameTube(0.03, HEAD_JOINT, STEM_BOT, m.black);                    // headset spacers
  frameTube(0.02, STEM_BOT, STEM_TOP, m.black);                      // stem
  const saddle = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.04, 0.25), m.saddle);
  saddle.position.set(0, SADDLE_TOP.y + 0.02, SADDLE_TOP.z - 0.02);
  bike.add(saddle);
  // handlebar: straight centre bar, a short connector from the stem top to it, then each side's corner to the STI
  frameTube(0.013, tiltBar(new V(-0.10, BAR_Y, BAR_Z)), tiltBar(new V(0.10, BAR_Y, BAR_Z)), m.black);
  frameTube(0.013, BAR_CLAMP, tiltBar(new V(0, BAR_Y, BAR_Z)), m.black);
  for (const s of [-1, 1]) {
    bike.add(new THREE.Mesh(new THREE.TubeGeometry(barCurve(s), 90, 0.012, 8, false), m.black));
  }
  const chainring = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 0.01, 14), m.black);
  chainring.rotation.z = Math.PI / 2;
  chainring.position.set(0.07, BB.y, BB.z);
  bike.add(chainring);

  // wheels: 75 mm section = 45 mm aero rim + 30 mm tyre. Black tread (the contact patch), white sidewalls,
  // hub. Each wheel group spins about its axle (x).
  const WHEEL_OUT = 0.3435;              // outer tread radius (ground contact unchanged)
  const RIM_IN = WHEEL_OUT - 0.075;      // rim base, 45 mm below the rim top
  const RIM_OUT = WHEEL_OUT - 0.030;     // rim top = tyre bead, the tyre is 30 mm tall
  const RIM_HALF = 0.0135;               // rim 27 mm wide
  const solidMat = (src, hex) => new THREE.MeshLambertMaterial({
    color: hex, side: THREE.DoubleSide, transparent: !!src.transparent,
    opacity: src.opacity === undefined ? 1 : src.opacity, depthWrite: src.depthWrite !== false,
  });
  const rimMat = solidMat(m.black, 0x1c1d20);
  const sidewallMat = solidMat(m.rubber, 0xe0c49a);   // retro creme-tan sidewalls, close to the rider's skin tone
  const discMat = solidMat(m.metal, 0x9aa2ab);
  // discX: the disc brake rotor sits beside the hub on this side
  const makeWheel = (zPos, discX) => {
    const g = new THREE.Group();
    g.position.set(0, 0, zPos);
    bike.add(g);
    // aero rim: two flat sides 45 mm deep, and the rim band
    for (const sx of [-1, 1]) {
      const side = new THREE.Mesh(new THREE.RingGeometry(RIM_IN, RIM_OUT, 48), rimMat);
      side.rotation.y = Math.PI / 2;
      side.position.x = sx * RIM_HALF;
      g.add(side);
    }
    const band = new THREE.Mesh(new THREE.CylinderGeometry(RIM_OUT, RIM_OUT, 2 * RIM_HALF, 48, 1, true), rimMat);
    band.rotation.z = Math.PI / 2;
    g.add(band);
    // tyre: black tread torus (30 mm tall) with white sidewall rings on both sides
    const tread = new THREE.Mesh(new THREE.TorusGeometry(RIM_OUT + 0.015, 0.015, 8, 48), m.rubber);
    tread.rotation.y = Math.PI / 2;
    g.add(tread);
    for (const sx of [-1, 1]) {
      const sidewall = new THREE.Mesh(new THREE.RingGeometry(RIM_OUT, WHEEL_OUT - 0.010, 48), sidewallMat);
      sidewall.rotation.y = Math.PI / 2;
      sidewall.position.x = sx * 0.0152;
      g.add(sidewall);
    }
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.0125, 0.0125, 0.12, 8), m.metal);   // half the old diameter
    hub.rotation.z = Math.PI / 2;
    g.add(hub);
    // 21 spokes from the hub to the rim base (visible through the open inside of the rim sides)
    const hubPt = new V(0, 0, 0);
    for (let i = 0; i < 21; i++) {
      const th = (i / 21) * Math.PI * 2;
      const base = new V(0, RIM_IN * Math.sin(th), RIM_IN * Math.cos(th));
      const sp = new THREE.Mesh(cylGeo(0.006), m.metal);
      setTube(sp, hubPt, base, UP);
      g.add(sp);
    }
    // disc brake rotor, 160 mm, on the side given by discX
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.003, 48), discMat);
    disc.rotation.z = Math.PI / 2;
    disc.position.x = discX;
    g.add(disc);
    return g;
  };
  const wheelRear = makeWheel(REAR.z, -0.035);   // rear rotor on the left, cassette on the right
  const wheelFront = makeWheel(FRONT.z, 0.035);
  // black cassette: a cone from the 34-tooth cog (radius 68 mm, inboard) out to the 11-tooth cog (radius 20 mm, outboard)
  // back where it was: x 0.02 .. 0.05, between the hub and the chainstays
  const cassette = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.068, 0.03, 24), m.black);
  cassette.rotation.z = -Math.PI / 2;   // the top (small cog) points outboard, +x
  cassette.position.set(0.035, 0, 0);
  wheelRear.add(cassette);

  // rider parts
  const tubeRider = (r, mm) => { const t = new THREE.Mesh(cylGeo(r), mm); bike.add(t); return t; };
  const boxRider = (w, h, mm) => { const b = new THREE.Mesh(boxGeo(w, h), mm); bike.add(b); return b; };
  const legs = {};
  for (const side of [1, -1]) {
    const pedal = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.025, 0.09), m.pedal);
    bike.add(pedal);
    legs[side] = {
      crank: tubeRider(0.012, m.metal),
      pedal,
      thigh: tubeRider(0.07, m.shorts),
      calf: tubeRider(0.045, m.skin),
      shin: tubeRider(0.045, m.sock),
      foot: boxRider(0.09, 0.05, m.shoe),
      upper: tubeRider(0.04, m.jersey),
      fore: tubeRider(0.035, m.skin),
    };
  }
  const torso = boxRider(0.3, 0.2, m.jersey);
  const neck = tubeRider(0.035, m.skin);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.1, 12, 10), m.skin);
  // helmet: only the top and back of a sphere (open at the front and bottom), so the face stays clear
  const helmet = new THREE.Mesh(new THREE.SphereGeometry(0.128, 18, 12, 0, Math.PI * 2, 0, Math.PI * 0.6), m.helmet);
  const visor = new THREE.Mesh(new THREE.BoxGeometry(0.13, 0.012, 0.07), m.black);
  bike.add(head, helmet, visor);

  function pose(phi, pedalling) {
    const S = HIP_MID.clone().addScaledVector(TORSO_DIR, TORSO_LEN);
    setTube(torso, HIP_MID, S, FWD);
    const headPos = S.clone().add(new V(0, 0.16, -0.06));
    setTube(neck, S, headPos, UP);
    head.position.copy(headPos);
    helmet.position.copy(headPos).add(new V(0, 0.02, 0));
    visor.position.copy(headPos).add(new V(0, 0.07, -0.1));
    visor.rotation.x = -0.25;
    for (const side of [1, -1]) {
      const L = legs[side];
      const pedal = pedalPos(side, pedalling ? phi : 0);
      L.pedal.position.copy(pedal);
      setTube(L.crank, new V(side * 0.09, BB.y, BB.z), pedal, UP);
      const hip = new V(side * HIP_X, HIP_MID.y, HIP_MID.z);
      const ankle = pedal.clone().add(new V(0, 0.07, 0.02));
      const toe = pedal.clone().add(new V(0, -0.02, -0.1));
      const knee = ikJoint(hip, ankle, THIGH, SHIN, new V(side * 0.25, 0, -1));
      setTube(L.thigh, hip, knee, UP);
      const calfTop = knee.clone().lerp(ankle, 0.6);   // skin above, sock below
      setTube(L.calf, knee, calfTop, UP);
      setTube(L.shin, calfTop, ankle, UP);
      setTube(L.foot, ankle, toe, FWD);
      const shoulder = S.clone().add(new V(side * 0.17, 0, 0));
      const hand = GRIPS[side > 0 ? 1 : 0];
      // elbows point down and back, only slightly out: a narrower, more aero arm position
      const elbow = ikJoint(shoulder, hand, UPPER_ARM, FOREARM, new V(side * 0.1, -0.2, 1.0));
      setTube(L.upper, shoulder, elbow, UP);
      setTube(L.fore, elbow, hand, UP);
    }
  }

  function setWheels(angle) {
    wheelRear.rotation.x = angle;
    wheelFront.rotation.x = angle;
  }

  bike.traverse((o) => { if (o.isMesh) { o.castShadow = castShadow; o.receiveShadow = false; } });
  return { group: bike, pose, setWheels };
}

export function createWorld(container, onFps = () => {}) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  } catch (e) {
    throw new Error("WebGL is not available in this browser: " + e.message);
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  container.prepend(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = SKY_HORIZON.clone();
  scene.fog = new THREE.Fog(SKY_HORIZON, 120, 480);
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1000);
  scene.add(new THREE.HemisphereLight(0xcfe8ff, 0x5a6b3a, 0.9));
  const sun = new THREE.DirectionalLight(0xfff1d6, 2.0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -22, right: 22, top: 22, bottom: -22, near: 1, far: 260 });
  sun.shadow.camera.updateProjectionMatrix();
  sun.shadow.bias = -0.0008;
  sun.shadow.normalBias = 0.03;
  scene.add(sun);
  scene.add(sun.target);

  // ---------- sky: gradient dome, sun disc ----------
  const skyGeo = new THREE.SphereGeometry(900, 24, 16);
  const skyPos = skyGeo.attributes.position;
  const skyCol = new Float32Array(skyPos.count * 3);
  for (let i = 0; i < skyPos.count; i++) {
    const t = Math.min(1, Math.max(0, (skyPos.getY(i) / 900) * 1.6));  // 0 at the horizon, 1 overhead
    const c = SKY_HORIZON.clone().lerp(SKY_TOP, t);
    skyCol[i * 3] = c.r; skyCol[i * 3 + 1] = c.g; skyCol[i * 3 + 2] = c.b;
  }
  skyGeo.setAttribute("color", new THREE.BufferAttribute(skyCol, 3));
  const sky = new THREE.Mesh(skyGeo, new THREE.MeshBasicMaterial({
    vertexColors: true, side: THREE.BackSide, fog: false, depthWrite: false,
  }));
  sky.renderOrder = -1;
  scene.add(sky);
  const sunDisc = new THREE.Mesh(new THREE.SphereGeometry(18, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0xfff4c8, fog: false }));
  sunDisc.position.copy(SUN_DIR).multiplyScalar(820);
  scene.add(sunDisc);

  // ---------- materials ----------
  const mat = {
    frame: new THREE.MeshPhongMaterial({ color: 0xd8261c, shininess: 90, specular: 0x777777 }),
    black: new THREE.MeshLambertMaterial({ color: 0x1c1d20 }),
    rubber: new THREE.MeshLambertMaterial({ color: 0x141517 }),
    metal: new THREE.MeshPhongMaterial({ color: 0x1a1a1a, shininess: 60 }),
    jersey: new THREE.MeshPhongMaterial({ color: 0x1e5fa8, shininess: 30 }),
    shorts: new THREE.MeshLambertMaterial({ color: 0x20283a }),
    skin: new THREE.MeshLambertMaterial({ color: 0xe3b594 }),
    sock: new THREE.MeshLambertMaterial({ color: 0xeeeeee }),
    helmet: new THREE.MeshPhongMaterial({ color: 0xffffff, shininess: 80 }),
    sleeve: new THREE.MeshPhongMaterial({ color: 0xffffff, shininess: 30 }),
    saddle: new THREE.MeshLambertMaterial({ color: 0x101010 }),
    shoe: new THREE.MeshLambertMaterial({ color: 0xffffff }),
    pedal: new THREE.MeshLambertMaterial({ color: 0x333333 }),
    pineLeaf: new THREE.MeshLambertMaterial({ color: 0x2d5a2b, flatShading: true }),
    broadLeaf: new THREE.MeshLambertMaterial({ color: 0x4f8a3a, flatShading: true }),
    bark: new THREE.MeshLambertMaterial({ color: 0x5a4030 }),
    post: new THREE.MeshLambertMaterial({ color: 0xf4f4f4 }),
    rock: new THREE.MeshLambertMaterial({ color: 0x7d7f84, flatShading: true }),
    mountain: new THREE.MeshLambertMaterial({ color: 0x55697f, flatShading: true }),
    snow: new THREE.MeshLambertMaterial({ color: 0xf4f7fb, flatShading: true }),
    cloud: new THREE.MeshLambertMaterial({ color: 0xffffff }),
    windmill: new THREE.MeshLambertMaterial({ color: 0xeeeeee }),
    blade: new THREE.MeshLambertMaterial({ color: 0xf6f6f6 }),
    windmillCap: new THREE.MeshLambertMaterial({ color: 0x5b3a29 }),
    sailCloth: new THREE.MeshLambertMaterial({ color: 0xf4f1e8 }),
  };
  // ghost: one translucent blue-grey material for every part
  const ghostMat = new THREE.MeshLambertMaterial({ color: 0x6f88a3, transparent: true, opacity: 0.38, depthWrite: false });
  const ghostMats = {};
  for (const k of Object.keys(mat)) ghostMats[k] = ghostMat;

  // ---------- road ground: vertex colours, flat shaded ----------
  const nV = ROWS * COLS;
  const gPos = new Float32Array(nV * 3), gCol = new Float32Array(nV * 3);
  const gGeom = new THREE.BufferGeometry();
  gGeom.setAttribute("position", new THREE.BufferAttribute(gPos, 3));
  gGeom.setAttribute("color", new THREE.BufferAttribute(gCol, 3));
  gGeom.setIndex(gridIndex(ROWS, COLS));
  const ground = new THREE.Mesh(gGeom, new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true }));
  ground.frustumCulled = false;
  ground.receiveShadow = true;
  scene.add(ground);

  // ---------- road markings: scrolling texture on an overlay ----------
  const markCanvas = document.createElement("canvas");
  markCanvas.width = 256; markCanvas.height = 256;
  {
    const g = markCanvas.getContext("2d");
    const X = (x) => ((x + 4.3) / 8.6) * 256; // same mapping as the overlay UVs
    g.clearRect(0, 0, 256, 256);
    g.fillStyle = "rgba(240,240,240,0.95)";   // white edge lines
    g.fillRect(X(3.0), 0, X(3.2) - X(3.0), 256);
    g.fillRect(X(-3.2), 0, X(-3.0) - X(-3.2), 256);
    g.fillStyle = "rgba(250,250,250,0.95)";   // white centre dashes: 3 of every 9 m
    g.fillRect(X(-0.1), 0, X(0.1) - X(-0.1), (256 * 3) / DASH_PERIOD);
  }
  const markTex = new THREE.CanvasTexture(markCanvas);
  markTex.wrapS = THREE.ClampToEdgeWrapping;
  markTex.wrapT = THREE.RepeatWrapping;
  const nO = ROWS * OX.length;
  const oPos = new Float32Array(nO * 3), oUv = new Float32Array(nO * 2);
  const oGeom = new THREE.BufferGeometry();
  oGeom.setAttribute("position", new THREE.BufferAttribute(oPos, 3));
  oGeom.setAttribute("uv", new THREE.BufferAttribute(oUv, 2));
  oGeom.setIndex(gridIndex(ROWS, OX.length));
  const overlay = new THREE.Mesh(oGeom, new THREE.MeshLambertMaterial({
    map: markTex, transparent: true, alphaTest: 0.2, flatShading: true,
    polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4,
  }));
  overlay.frustumCulled = false;
  overlay.receiveShadow = true;
  scene.add(overlay);
  for (let r = 0; r < ROWS; r++) {
    for (let j = 0; j < OX.length; j++) {
      const i = r * OX.length + j;
      oUv[i * 2] = (OX[j] + 4.3) / 8.6;
    }
  }

  const roadY = new Float64Array(ROWS);

  // ---------- scenery ----------
  const CELL = 25, TREE_MAX = 160;
  const pineCone = new THREE.InstancedMesh(new THREE.ConeGeometry(1.4, 4, 7), mat.pineLeaf, TREE_MAX);
  const pineTrunk = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.2, 0.25, 2, 6), mat.bark, TREE_MAX);
  const broadCrown = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1.8, 0), mat.broadLeaf, TREE_MAX);
  const broadTrunk = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.22, 0.3, 2.4, 6), mat.bark, TREE_MAX);
  const posts = new THREE.InstancedMesh(new THREE.BoxGeometry(0.12, 1.1, 0.12), mat.post, 120);
  const rocks = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(0.5, 0), mat.rock, 90);
  // pole reflectors: red on the right of the road (+x), white on the left (-x)
  const reflectors = new THREE.InstancedMesh(new THREE.BoxGeometry(0.04, 0.14, 0.14),
    new THREE.MeshLambertMaterial({ color: 0xffffff }), 120);
  reflectors.frustumCulled = false;
  scene.add(reflectors);
  const REFLECTOR_RED = new THREE.Color(0xd62828), REFLECTOR_WHITE = new THREE.Color(0xffffff);
  for (const im of [pineCone, pineTrunk, broadCrown, broadTrunk, posts, rocks]) {
    im.frustumCulled = false;
    im.castShadow = true;
    scene.add(im);
  }
  // distant mountains with snow caps (static backdrop, faded by the fog)
  for (let i = 0; i < 30; i++) {
    const h = 70 + hash(i + 3) * 120, rad = 55 + hash(i + 9) * 70;
    const x = (hash(i) - 0.5) * 760, z = -330 - hash(i + 5) * 130;
    const m = new THREE.Mesh(new THREE.ConeGeometry(rad, h, 7), mat.mountain);
    m.position.set(x, h / 2 - 2, z);
    scene.add(m);
    if (h > 110) {
      const s = new THREE.Mesh(new THREE.ConeGeometry(rad * 0.32, h * 0.22, 7), mat.snow);
      s.position.set(x, h - h * 0.11 - 2, z);
      scene.add(s);
    }
  }
  // puffy clouds: clusters of spheres, drifting slowly across the sky
  const clouds = [];
  const puffGeo = new THREE.SphereGeometry(1, 12, 8);
  for (let i = 0; i < 14; i++) {
    const g = new THREE.Group();
    const puffs = 4 + Math.floor(hash(i + 90) * 4);
    for (let p = 0; p < puffs; p++) {
      const pm = new THREE.Mesh(puffGeo, mat.cloud);
      const sc = 6 + hash(i * 31 + p) * 7;
      pm.scale.set(sc, sc * 0.6, sc * 0.8);
      pm.position.set((p - puffs / 2) * 7 + (hash(i * 17 + p) - 0.5) * 4, hash(i * 13 + p) * 3, (hash(i * 7 + p) - 0.5) * 6);
      g.add(pm);
    }
    g.position.set((hash(i + 60) - 0.5) * 600, 85 + hash(i + 70) * 45, -140 - hash(i + 80) * 260);
    g.userData.speed = 0.8 + hash(i + 3) * 1.2;
    scene.add(g);
    clouds.push(g);
  }

  // ---------- scenery: windmills, hot-air balloons, birds ----------
  // Each object is anchored to an absolute distance along the road, so it scrolls past as the
  // rider moves; updateScenery() places it relative to the rider every frame.
  const windmills = [];
  for (let i = 0; i < 12; i++) {
    const g = new THREE.Group();
    // classic Dutch smock mill: wide tapered white tower, brown domed cap, four large lattice sails
    const tower = new THREE.Mesh(new THREE.CylinderGeometry(2.4, 4.2, 20, 12), mat.windmill);
    tower.position.y = 10;
    tower.castShadow = true;
    g.add(tower);
    const cap = new THREE.Mesh(new THREE.SphereGeometry(2.6, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), mat.windmillCap);
    cap.position.y = 20;
    cap.castShadow = true;
    g.add(cap);
    const rotor = new THREE.Group();
    rotor.position.set(0, 21.5, 2.7);
    g.add(rotor);
    const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.6, 0.6, 1.0, 10), mat.windmillCap);
    hub.rotation.x = Math.PI / 2;
    rotor.add(hub);
    for (let b = 0; b < 4; b++) {
      const arm = new THREE.Group();
      arm.rotation.z = (b * Math.PI) / 2;
      // lattice sail: a long spar with a cloth panel on one side
      const spar = new THREE.Mesh(new THREE.BoxGeometry(0.3, 13, 0.2), mat.blade);
      spar.position.y = 6.8;
      spar.castShadow = true;
      arm.add(spar);
      const cloth = new THREE.Mesh(new THREE.BoxGeometry(2.6, 9, 0.05), mat.sailCloth);
      cloth.position.set(0.9, 6.0, 0);
      cloth.castShadow = true;
      arm.add(cloth);
      rotor.add(arm);
    }
    scene.add(g);
    windmills.push({
      group: g, rotor,
      d: 90 + i * 140 + hash(i + 300) * 60,
      x: (i % 2 ? 1 : -1) * (18 + hash(i + 310) * 14),
      spin: hash(i + 320) * 6,
    });
  }

  const balloonCols = [0xd7263d, 0xf4a300, 0x2e86ab, 0x7bc043, 0xf2f2f2, 0x8e44ad];
  const ropeMat = new THREE.MeshLambertMaterial({ color: 0x4a3a2a });
  const balloons = [];
  for (let i = 0; i < 7; i++) {
    const g = new THREE.Group();
    const env = new THREE.Mesh(new THREE.SphereGeometry(5, 18, 12),
      new THREE.MeshLambertMaterial({ color: balloonCols[i % balloonCols.length], flatShading: true }));
    env.scale.set(1, 1.2, 1);
    g.add(env);
    const basket = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.1, 1.4), mat.bark);
    basket.position.y = -7.6;
    g.add(basket);
    for (const [sx, sz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const rope = new THREE.Mesh(cylGeo(0.04), ropeMat);
      setTube(rope, new V(sx * 0.7, -7.0, sz * 0.7), new V(sx * 2.2, -3.2, sz * 2.2), UP);
      g.add(rope);
    }
    scene.add(g);
    balloons.push({
      group: g,
      d: 260 + i * 210 + hash(i + 400) * 80,
      x: (i % 2 ? 1 : -1) * (40 + hash(i + 410) * 120),
      y: 60 + hash(i + 420) * 50,
      phase: hash(i + 430) * 6,
    });
  }

  const birdMat = new THREE.MeshLambertMaterial({ color: 0x2b2f36, side: THREE.DoubleSide });
  const birds = [];
  for (let i = 0; i < 20; i++) {
    const g = new THREE.Group();
    const wings = [];
    for (const s of [-1, 1]) {
      const pivot = new THREE.Group();
      const wing = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.04, 0.22), birdMat);
      wing.position.x = s * 0.45;
      pivot.add(wing);
      g.add(pivot);
      wings.push({ pivot, s });
    }
    scene.add(g);
    birds.push({
      group: g, wings,
      d: 60 + hash(i + 500) * 420,
      x: (hash(i + 510) - 0.5) * 260,
      y: 35 + hash(i + 520) * 45,
      phase: hash(i + 530) * 6,
      vx: (hash(i + 540) < 0.5 ? -1 : 1) * (1 + hash(i + 550) * 2),
    });
  }

  // airships: long envelope along the road, tail fins, gondola and struts; they drift slowly across the sky
  const airships = [];
  const envMats = [0xd9d2c3, 0xb9c4cf, 0xe0c9a6].map((c) => new THREE.MeshLambertMaterial({ color: c, flatShading: true }));
  for (let i = 0; i < 3; i++) {
    const g = new THREE.Group();
    g.rotation.y = Math.PI / 2;                         // long axis along the road
    const env = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 16), envMats[i]);
    env.scale.set(14, 4.5, 4.5);
    g.add(env);
    const finH = new THREE.Mesh(new THREE.BoxGeometry(4.5, 0.15, 5), mat.windmill);
    finH.position.set(-12, 0, 0);
    g.add(finH);
    const finV = new THREE.Mesh(new THREE.BoxGeometry(4.5, 5, 0.15), mat.windmill);
    finV.position.set(-12, 0, 0);
    g.add(finV);
    const gondola = new THREE.Mesh(new THREE.BoxGeometry(5, 1.6, 1.6), mat.bark);
    gondola.position.set(0, -6.2, 0);
    g.add(gondola);
    for (const sx of [-4, 4]) {
      const strut = new THREE.Mesh(cylGeo(0.12), mat.bark);
      setTube(strut, new V(sx, -4.4, 0), new V(sx, -5.4, 0), UP);
      g.add(strut);
    }
    scene.add(g);
    airships.push({
      group: g,
      d: 400 + i * 420 + hash(i + 600) * 120,
      x: (i % 2 ? 1 : -1) * (60 + hash(i + 610) * 90),
      y: 45 + hash(i + 620) * 18,
      phase: hash(i + 630) * 6,
    });
  }

  let sceneT = 0;
  // Scenery repeats along the road: an object's distance wraps every `period` metres, so once the
  // rider has passed it, it comes back further ahead instead of disappearing for good.
  // Returns the distance relative to the rider, in [-back, period - back).
  const wrapRel = (d, dNow, back, period) => ((((d - dNow + back) % period) + period) % period) - back;
  function updateScenery(dNow, dt) {
    sceneT += dt;
    for (const w of windmills) {
      const rel = wrapRel(w.d, dNow, D_BACK, 1680);   // 12 mills, 140 m apart
      const show = rel >= -D_BACK && rel <= D_AHEAD;
      w.group.visible = show;
      if (show) {
        w.group.position.set(w.x, meshHeightAt(w.x, rel, dNow), -rel);
        w.rotor.rotation.z = w.spin + sceneT * 1.1;   // slow turning, the blades face the road
      }
    }
    for (const b of balloons) {
      const rel = wrapRel(b.d, dNow, 30, 1470);       // 7 balloons, 210 m apart
      const show = rel >= -30 && rel <= 450;
      b.group.visible = show;
      if (show) {
        b.group.position.set(b.x + Math.sin(sceneT * 0.1 + b.phase) * 3, b.y, -rel);
      }
    }
    for (const a of airships) {
      const rel = wrapRel(a.d, dNow, 60, 1260);       // 3 airships, 420 m apart
      const show = rel >= -60 && rel <= 500;
      a.group.visible = show;
      if (show) a.group.position.set(a.x + Math.sin(sceneT * 0.05 + a.phase) * 6, a.y, -rel);
    }
    for (const bd of birds) {
      bd.x += bd.vx * dt;
      if (bd.x > 160) bd.x = -160;
      if (bd.x < -160) bd.x = 160;
      const rel = wrapRel(bd.d, dNow, 20, 420);       // birds: anchored 60-480 m, repeat every 420 m
      const show = rel >= -20 && rel <= 350;
      bd.group.visible = show;
      if (show) {
        bd.group.position.set(bd.x, bd.y + Math.sin(sceneT * 0.5 + bd.phase) * 0.8, -rel);
        const flap = Math.sin(sceneT * 7 + bd.phase) * 0.6;   // wings beat up and down
        for (const wg of bd.wings) wg.pivot.rotation.z = wg.s * flap;
      }
    }
  }

  // ---------- rider and ghost ----------
  const rider = buildRig(mat, true);
  const bike = rider.group;
  bike.position.set(LANE_X, WHEEL_R, 0);
  scene.add(bike);
  // ghost stream: ghosts spawn at random, just out of sight, ahead or behind, in the left lane.
  // parameterized: each ghost follows its level's virtual profile (interval power -10..+10%).
  // adaptive: each ghost keeps a share of YOUR average (speed, or power converted to speed).
  // Colours: above 0 = yellow jersey, below 0 = green jersey, 0 = the usual ghost colour.
  const ghostJersey = (c) => new THREE.MeshLambertMaterial({ color: c, transparent: true, opacity: 0.5, depthWrite: false });
  // jersey colour per level (percent of interval power): +10 yellow, +5 pink, 0 red, -5 white, -10 green
  const LEVEL_JERSEY = { "10": 0xf2c200, "5": 0xff69b4, "0": 0xd62828, "-5": 0xf2f2f2, "-10": 0x2ea043 };
  const ghostRigs = {};
  for (const key of Object.keys(LEVEL_JERSEY)) {
    const ms = { ...ghostMats, jersey: ghostJersey(LEVEL_JERSEY[key]) };
    ghostRigs[key] = [];
    for (let i = 0; i < 4; i++) {
      const rig = buildRig(ms, false);
      rig.group.visible = false;
      scene.add(rig.group);
      ghostRigs[key].push({ rig, busy: false });
    }
  }
  const ghostInstances = [];
  let spawnIn = 3;                  // seconds until the next ghost
  const GHOST_RATIO = 2.6;          // gear used for the ghosts' legs
  const ADAPTIVE_LIFE_S = 240;      // an adaptive ghost leaves after 4 minutes of riding
  const POWER_EXP = 1.9;            // trainer power curve exponent (power ~ speed^1.9)
  const speedHist = [];             // {t, v}: your virtual speed (m/s) over the last 60 s
  function averageVirtualSpeed(nowS) {
    const recent = speedHist.filter((p) => p.t >= nowS - 60);
    if (!recent.length) return 0;
    return recent.reduce((a, p) => a + p.v, 0) / recent.length;
  }
  function clearGhosts() {
    for (const gi of ghostInstances) { gi.entry.busy = false; gi.entry.rig.group.visible = false; }
    ghostInstances.length = 0;
  }
  // lateral slots 0.5 m apart. None sits on the centre marker (x = 0) or next to the rider (x = 2.5).
  // Left lane: -2.25 .. -0.75; right lane, up to the rider: 0.75 and 1.25.
  const GHOST_SLOTS = [-2.25, -1.75, -1.25, -0.75, 0.75, 1.25];
  // a slot is free if no live ghost in it is within 2.5 m (along the road) of the spawn point.
  // Fast ghosts (positive levels) prefer the free slot nearest the centre, slow ones the outermost left slot.
  function pickLane(lvl, spawnDelta) {
    const clear = (x) => ghostInstances.every((g) => g.x !== x || Math.abs(g.delta - spawnDelta) > 2.5);
    const free = GHOST_SLOTS.filter(clear);
    if (!free.length) return GHOST_SLOTS[Math.floor(Math.random() * GHOST_SLOTS.length)];
    if (lvl > 0) return free.reduce((a, b) => (Math.abs(b) < Math.abs(a) ? b : a));
    if (lvl < 0) return free.reduce((a, b) => (Math.abs(b) > Math.abs(a) ? b : a));
    return free[Math.floor(Math.random() * free.length)];
  }
  function spawnGhost(tNow, dNow) {
    const keys = ghostMode === "param"
      ? (ghostLevels ? Object.keys(ghostLevels) : [])
      : ["-10", "-5", "0", "5", "10"];
    if (!keys.length) return;
    const lvl = parseInt(keys[Math.floor(Math.random() * keys.length)], 10);
    const entry = ghostRigs[String(lvl)].find((e) => !e.busy);
    if (!entry) return;
    entry.busy = true;
    const ahead = Math.random() < 0.5;
    const spawnDelta = ahead ? D_AHEAD + 20 : -(D_BACK + 15);       // just out of sight
    ghostInstances.push({
      key: String(lvl), lvl, entry, mode: ghostMode,
      x: pickLane(lvl, spawnDelta), delta: spawnDelta,
      spawnAbs: dNow + spawnDelta,
      tSpawn: tNow, travel: 0, life: 0, wheel: 0, crank: 0,
    });
  }

  // ---------- camera and state ----------
  const camTarget = new V(LANE_X, 1, -12);
  camera.position.set(LANE_X, 2.6, 6.5);
  camera.lookAt(camTarget);

  let profile = null;              // gradient_per_second, percent per second
  let ghostOn = false;             // ghost rider option (off by default)
  let ghostDistance = null;        // cumulative ghost distance (m) per second, from the workout
  let ghostSpeedKmh = null;        // ghost speed (km/h) per second, from the workout
  let ghostCadenceRpm = null;      // ghost virtual cadence (rpm) per second, from the workout
  let ghostLevels = null;          // per-level ghost profiles (-10..+10% of interval power), from the workout
  let ghostMode = "param";         // "param" | "speed" | "power"

  // linear interpolation in a per-second array; before the start the first value, after the end the last
  function interpArr(arr, t) {
    if (t <= 0) return arr[0];
    if (t >= arr.length - 1) return arr[arr.length - 1];
    const i = Math.floor(t), f = t - i;
    return arr[i] + (arr[i + 1] - arr[i]) * f;
  }
  // the ghost stops when the workout ends
  function interpSpeed(arr, t) {
    if (t >= arr.length - 1) return 0;
    return interpArr(arr, t);
  }
  let snap = null, snapAt = 0;     // latest server snapshot and when it arrived (s)
  let vSmooth = 0;                 // m/s, EMA of speed
  let wheelAngle = 0, crankAngle = 0;
  let ghostWheel = 0, ghostCrank = 0;
  let w = 0, h = 0;
  let frames = 0, fpsT0 = performance.now() / 1000;
  let lastFrame = performance.now() / 1000;

  // gradient in percent, interpolated between whole seconds so the slope never steps
  const gradAt = (t) => {
    if (!profile || t < 0 || t >= profile.length - 1) return 0;
    const i = Math.floor(t), f = t - i;
    return profile[i] + (profile[i + 1] - profile[i]) * f;
  };

  // ground offset across the columns, interpolated exactly like the ground mesh triangles
  function colOffset(x, dAbs) {
    let c = 0;
    while (c < COLS - 2 && XS[c + 1] < x) c++;
    const x0 = XS[c], x1 = XS[c + 1];
    const f = Math.min(1, Math.max(0, (x - x0) / (x1 - x0)));
    const o = (xx) => (Math.abs(xx) > ROAD_HALF ? groundOffset(xx, dAbs) : 0);
    return o(x0) + (o(x1) - o(x0)) * f;
  }

  // height of the ground mesh at lateral x and rider-relative distance d, so objects sit on it
  function meshHeightAt(x, d, dNow) {
    const [k, f] = rowIndexFor(d);
    const h0 = roadY[k] + colOffset(x, dNow + rowD[k]);
    const h1 = roadY[k + 1] + colOffset(x, dNow + rowD[k + 1]);
    return h0 + (h1 - h0) * f;
  }

  // Rider and ghost time base. The page advances time every frame and eases towards the server's
  // estimate, so a snapshot arriving late or early does not make the riders jump.
  let disp = null, dispAt = 0;
  function timeNow(nowS) {
    // no ride data yet (status "none" has no elapsed time): keep the road still and drop the estimate
    if (!snap || !Number.isFinite(snap.elapsed_s)) { disp = null; return { t: 0, dist: 0, speed: 0 }; }
    const running = snap.status === "running" || snap.status === "preview";   // preview: live physics, no ride
    const ahead = running ? Math.max(0, nowS - snapAt) : 0;
    // animation follows the virtual speed (road physics), not the measured sensor speed
    const speed = running ? (snap.virtual_speed_kmh || 0) / 3.6 : 0;
    const target = { t: snap.elapsed_s + ahead, dist: (snap.virtual_distance_m || 0) + speed * ahead };
    if (!disp || !Number.isFinite(disp.t) || target.t < disp.t - 2) { disp = { ...target }; dispAt = nowS; }   // first snapshot or a new ride
    const dt = Math.max(0, Math.min(0.1, nowS - dispAt));
    dispAt = nowS;
    if (running) { disp.t += dt; disp.dist += speed * dt; }
    const k = 1 - Math.exp(-dt * 4);   // gentle correction, time constant about 0.25 s
    disp.t += (target.t - disp.t) * k;
    disp.dist += (target.dist - disp.dist) * k;
    return { t: disp.t, dist: disp.dist, speed };
  }

  // ---------- per-frame road, markings and scenery ----------
  const _p = new V(), _s = new V(), _q = new THREE.Quaternion(), _mx = new THREE.Matrix4();
  // reflectors face the road edge, turned 60 degrees towards the rider (+z): normal = (-side*0.5, 0, 0.866)
  const REFLECTOR_ROT = {
    "-1": new THREE.Quaternion().setFromUnitVectors(new V(1, 0, 0), new V(0.5, 0, 0.866)),
    "1": new THREE.Quaternion().setFromUnitVectors(new V(1, 0, 0), new V(-0.5, 0, 0.866)),
  };
  const putQ = (im, idx, x, y, z, s, q) => {
    _p.set(x, y, z);
    _s.set(s, s, s);
    _mx.compose(_p, q, _s);
    im.setMatrixAt(idx, _mx);
  };
  const put = (im, idx, x, y, z, s) => {
    _p.set(x, y, z);
    _s.set(s, s, s);
    _mx.compose(_p, _q, _s);
    im.setMatrixAt(idx, _mx);
  };

  function rebuildRoad(tNow, dNow, vPred) {
    const G = new Float64Array(ROWS - 1);
    for (let k = 0; k < ROWS - 1; k++) G[k] = gradAt(tNow + (rowD[k] + SEG[k] / 2) / vPred);
    roadY[R0] = 0;
    for (let k = R0; k < ROWS - 1; k++) roadY[k + 1] = roadY[k] + (G[k] * SEG[k]) / 100;
    for (let k = R0 - 1; k >= 0; k--) roadY[k] = roadY[k + 1] - (G[k] * SEG[k]) / 100;

    // ground
    for (let r = 0; r < ROWS; r++) {
      const dAbs = dNow + rowD[r];
      for (let c = 0; c < COLS; c++) {
        const x = XS[c], ax = Math.abs(x);
        const hOff = ax > ROAD_HALF ? groundOffset(x, dAbs) : 0;
        const i = (r * COLS + c) * 3;
        gPos[i] = x;
        gPos[i + 1] = roadY[r] + hOff;
        gPos[i + 2] = -rowD[r];
        if (ax <= ROAD_HALF) {          // asphalt
          gCol[i] = 0.23; gCol[i + 1] = 0.24; gCol[i + 2] = 0.27;
        } else if (ax <= 4.3) {         // gravel shoulder
          gCol[i] = 0.56; gCol[i + 1] = 0.53; gCol[i + 2] = 0.46;
        } else {                        // grass, varying slowly with distance
          const n = vnoise(dAbs * 0.03 + ax * 0.05);
          gCol[i] = 0.22 + 0.06 * n; gCol[i + 1] = 0.42 + 0.14 * n; gCol[i + 2] = 0.18 + 0.04 * n;
        }
      }
    }
    gGeom.attributes.position.needsUpdate = true;
    gGeom.attributes.color.needsUpdate = true;

    // markings overlay: same rows, UV.v = distance / period so the texture scrolls smoothly
    for (let r = 0; r < ROWS; r++) {
      const dAbs = dNow + rowD[r];
      for (let j = 0; j < OX.length; j++) {
        const i = r * OX.length + j;
        oPos[i * 3] = OX[j];
        oPos[i * 3 + 1] = roadY[r] + 0.02;
        oPos[i * 3 + 2] = -rowD[r];
        oUv[i * 2 + 1] = dAbs / DASH_PERIOD;
      }
    }
    oGeom.attributes.position.needsUpdate = true;
    oGeom.attributes.uv.needsUpdate = true;

    // trees: deterministic per 25 m cell, so they scroll past instead of popping
    let pc = 0, bc = 0;
    for (let k = Math.floor((dNow - D_BACK) / CELL); k <= Math.floor((dNow + D_AHEAD) / CELL); k++) {
      for (let j = 0; j < 2; j++) {
        const seed = k * 2 + j;
        const dAbsT = k * CELL + hash(seed * 3 + 1) * CELL;
        const dRel = dAbsT - dNow;
        if (dRel < -D_BACK || dRel > D_AHEAD) continue;
        const side = hash(seed * 3 + 2) < 0.5 ? -1 : 1;
        const x = side * (ROAD_HALF + 4 + hash(seed * 3 + 3) * 22);
        const base = meshHeightAt(x, dRel, dNow);
        const s = 0.8 + hash(seed * 3 + 4) * 0.9;
        if (hash(seed * 7 + 5) < 0.55) {
          if (pc < TREE_MAX) {
            put(pineTrunk, pc, x, base + s, -dRel, s);
            put(pineCone, pc, x, base + 4 * s, -dRel, s);
            pc++;
          }
        } else if (bc < TREE_MAX) {
          put(broadTrunk, bc, x, base + 1.2 * s, -dRel, s);
          put(broadCrown, bc, x, base + 3.9 * s, -dRel, s);
          bc++;
        }
      }
    }
    pineCone.count = pineTrunk.count = pc;
    broadCrown.count = broadTrunk.count = bc;
    for (const im of [pineCone, pineTrunk, broadCrown, broadTrunk]) im.instanceMatrix.needsUpdate = true;

    // fence posts every 8 m on both sides (strong motion cue)
    let np = 0;
    for (let k = Math.floor((dNow - D_BACK) / 8); k <= Math.floor((dNow + D_AHEAD) / 8); k++) {
      const dRel = k * 8 - dNow;
      if (dRel < -D_BACK || dRel > D_AHEAD) continue;
      for (const side of [-1, 1]) {
        if (np < 120) {
          const px = side * 4.9, py = meshHeightAt(px, dRel, dNow);
          put(posts, np, px, py + 0.55, -dRel, 1);
          putQ(reflectors, np, px - side * 0.07, py + 0.85, -dRel, 1, REFLECTOR_ROT[side]);   // on the road side, turned 60 deg to the rider
          reflectors.setColorAt(np, side > 0 ? REFLECTOR_RED : REFLECTOR_WHITE);
          np++;
        }
      }
    }
    posts.count = np;
    posts.instanceMatrix.needsUpdate = true;
    reflectors.count = np;
    reflectors.instanceMatrix.needsUpdate = true;
    if (reflectors.instanceColor) reflectors.instanceColor.needsUpdate = true;

    // rocks along the verge, one candidate per 15 m cell
    let nr = 0;
    for (let k = Math.floor((dNow - D_BACK) / 15); k <= Math.floor((dNow + D_AHEAD) / 15); k++) {
      const dAbsR = k * 15 + hash(k * 5 + 1) * 15;
      const dRel = dAbsR - dNow;
      if (dRel < -D_BACK || dRel > D_AHEAD || hash(k * 5 + 2) < 0.4) continue;
      const side = hash(k * 5 + 3) < 0.5 ? -1 : 1;
      const x = side * (6 + hash(k * 5 + 4) * 18);
      const s = 0.4 + hash(k * 5 + 5) * 1.2;
      if (nr < 90) { put(rocks, nr, x, meshHeightAt(x, dRel, dNow) + 0.1 * s, -dRel, s); nr++; }
    }
    rocks.count = nr;
    rocks.instanceMatrix.needsUpdate = true;

    return G;
  }

  function resize() {
    const W = container.clientWidth || 1, H = container.clientHeight || 1;
    if (W === w && H === h) return;
    w = W; h = H;
    renderer.setSize(W, H, false);
    camera.aspect = W / H;
    camera.updateProjectionMatrix();
  }

  // ---------- camera navigation ----------
  // The rider stays at the origin; the camera orbits around a target near the rider.
  // yaw/pitch/dist describe the orbit, pan moves the target. Chase mode eases back behind the bike.
  const NAV_DEFAULT = { yaw: 0, pitch: 0.3, dist: 7 };
  const nav = { yaw: 0, pitch: 0.3, dist: 7, pan: new V(0, 0, 0), chase: true };
  const el = renderer.domElement;
  let drag = null;
  el.style.touchAction = "none";

  function resetView() {
    nav.yaw = NAV_DEFAULT.yaw;
    nav.pitch = NAV_DEFAULT.pitch;
    nav.dist = NAV_DEFAULT.dist;
    nav.pan.set(0, 0, 0);
    nav.chase = true;
  }
  const leaveChase = () => { nav.chase = false; };

  el.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey };
    el.setPointerCapture(e.pointerId);
    leaveChase();
  });
  el.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX;
    drag.y = e.clientY;
    if (drag.pan) {
      const s = nav.dist * 0.0015;
      const right = new V(Math.cos(nav.yaw), 0, -Math.sin(nav.yaw));
      nav.pan.addScaledVector(right, -dx * s).add(new V(0, dy * s, 0));
    } else {
      nav.yaw -= dx * 0.005;
      nav.pitch = Math.min(1.45, Math.max(-0.1, nav.pitch + dy * 0.005));
    }
  });
  const endDrag = () => { drag = null; };
  el.addEventListener("pointerup", endDrag);
  el.addEventListener("pointercancel", endDrag);
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("dblclick", resetView);
  el.addEventListener("wheel", (e) => {
    e.preventDefault();
    nav.dist = Math.min(60, Math.max(1.5, nav.dist * Math.exp(e.deltaY * 0.001)));
    leaveChase();
  }, { passive: false });

  window.addEventListener("keydown", (e) => {
    if (e.target && e.target.matches && e.target.matches("input,select,textarea,button")) return;
    const step = 0.05;
    switch (e.key.toLowerCase()) {
      case "arrowleft": nav.yaw += 0.08; break;
      case "arrowright": nav.yaw -= 0.08; break;
      case "arrowup": nav.pitch = Math.min(1.45, nav.pitch + 0.05); break;
      case "arrowdown": nav.pitch = Math.max(-0.1, nav.pitch - 0.05); break;
      case "+": case "=": nav.dist = Math.max(1.5, nav.dist * 0.9); break;
      case "-": case "_": nav.dist = Math.min(60, nav.dist * 1.1); break;
      case "w": nav.pan.y += step * nav.dist * 0.2; break;
      case "s": nav.pan.y -= step * nav.dist * 0.2; break;
      case "a": nav.pan.x -= step * nav.dist * 0.2; break;
      case "d": nav.pan.x += step * nav.dist * 0.2; break;
      case "r": resetView(); return;
      case "c": if (nav.chase) leaveChase(); else nav.chase = true; return;
      default: return;
    }
    leaveChase();
    e.preventDefault();
  });

  function tick() {
    requestAnimationFrame(tick);
    const nowS = performance.now() / 1000;
    const dt = Math.min(0.1, nowS - lastFrame);
    lastFrame = nowS;

    resize();
    const { t: tNow, dist: dNow, speed } = timeNow(nowS);
    const active = !!snap && (snap.status === "running" || snap.status === "paused" || snap.status === "finished");
    const previewing = !!snap && snap.status === "preview";   // sensors drive the bike before a ride starts
    const vCur = active || previewing ? speed : 0;
    vSmooth += (vCur - vSmooth) * (1 - Math.exp(-dt / 1.5));
    const vPred = Math.max(vSmooth, MIN_V);

    // before the first workout starts, use a time far in the past so every gradient reads as 0 (flat)
    const grads = rebuildRoad(active ? tNow : -1e9, dNow, vPred);

    // rider: pitch follows the gradient just ahead; cadence is the real sensor or a virtual one from speed
    bike.rotation.x = Math.atan((grads[R0] || 0) / 100);
    const live = !!snap && (snap.status === "running" || previewing);
    const realCadence = live && snap.cadence_rpm ? snap.cadence_rpm : null;
    const cadence = realCadence !== null
      ? realCadence
      : live ? (snap.virtual_cadence_rpm || 0) : 0;   // virtual gearing keeps it in 75-110 rpm
    // forward is -z, so the wheels must turn the other way round the x axis
    wheelAngle -= (vCur * dt) / WHEEL_R;
    rider.setWheels(wheelAngle);
    crankAngle += (cadence / 60) * Math.PI * 2 * dt;
    // no up/down bounce: the bike stays on the road. It rocks left and right in sync with the pedals
    // (one roll per crank turn, peaking when the crank is at 3 and 9 o'clock).
    bike.position.y = WHEEL_R;
    bike.rotation.z = cadence > 0 ? -ROCK_RAD * Math.sin(crankAngle) : 0;   // sign flipped: the roll ran the wrong way
    rider.pose(crankAngle, cadence > 0);

    // ghost stream: spawn at random, then move and draw every ghost
    const ghostRunning = ghostOn && !!snap && snap.status === "running";
    if (!ghostOn) clearGhosts();
    if (ghostRunning && (ghostMode !== "param" || ghostLevels)) {
      spawnIn -= dt;
      if (spawnIn <= 0) { spawnGhost(tNow, dNow); spawnIn = 4 + Math.random() * 6; }
    }
    const avgV = averageVirtualSpeed(nowS);                           // m/s, your average over the last minute
    for (let i = ghostInstances.length - 1; i >= 0; i--) {
      const gi = ghostInstances[i];
      let speed = 0, cad = 0, delta = 0, end = false;
      if (gi.mode === "param") {
        const L = ghostLevels && ghostLevels[gi.key];
        if (!L || tNow > L.distance_m.length - 1) {
          end = true;                                                 // the workout has ended for this ghost
        } else {
          const dist = gi.spawnAbs + interpArr(L.distance_m, tNow) - interpArr(L.distance_m, gi.tSpawn);
          delta = dist - dNow;
          speed = interpSpeed(L.speed_kmh, tNow) / 3.6;
          cad = interpSpeed(L.cadence_rpm, tNow);
        }
      } else {
        // adaptive: a share of your average speed, or of your power converted through the power curve
        const factor = gi.mode === "speed" ? 1 + gi.lvl / 100 : Math.pow(1 + gi.lvl / 100, 1 / POWER_EXP);
        speed = factor * avgV;
        if (ghostRunning) { gi.travel += speed * dt; gi.life += dt; }
        delta = gi.spawnAbs + gi.travel - dNow;
        cad = (speed * 60) / (GHOST_RATIO * WHEEL_CIRC);
        if (gi.life > ADAPTIVE_LIFE_S) end = true;
      }
      gi.delta = delta;
      if (!end && (delta < -(D_BACK + 150) || delta > D_AHEAD + 200)) end = true;   // far out of reach
      if (end || !ghostOn) {
        gi.entry.busy = false;
        gi.entry.rig.group.visible = false;
        ghostInstances.splice(i, 1);
        continue;
      }
      const rig = gi.entry.rig;
      gi.wheel -= (speed * dt) / WHEEL_R;
      gi.crank += (cad / 60) * Math.PI * 2 * dt;
      rig.setWheels(gi.wheel);
      rig.pose(gi.crank, speed > 0);
      const show = delta >= -D_BACK && delta <= D_AHEAD;
      rig.group.visible = show;
      if (!show) continue;
      rig.group.position.set(gi.x, meshHeightAt(gi.x, delta, dNow) + WHEEL_R, -delta);
      const slope = (meshHeightAt(gi.x, delta + 1, dNow) - meshHeightAt(gi.x, delta - 1, dNow)) / 2;
      rig.group.rotation.x = Math.atan(slope);
    }

    // sun and shadows follow the riders
    sun.target.position.set(0, 0, 0);
    sun.position.copy(SUN_DIR).multiplyScalar(100);

    updateScenery(dNow, dt);

    // clouds drift slowly and wrap around
    for (const c of clouds) {
      c.position.x += dt * c.userData.speed;
      if (c.position.x > 320) c.position.x -= 640;
    }

    // camera: orbit around the rider; chase mode eases back behind the bike
    if (nav.chase) {
      const k = 1 - Math.exp(-dt * 3);
      nav.yaw += (NAV_DEFAULT.yaw - nav.yaw) * k;
      nav.pitch += (NAV_DEFAULT.pitch - nav.pitch) * k;
      nav.dist += (NAV_DEFAULT.dist - nav.dist) * k;
      nav.pan.multiplyScalar(1 - k);
    }
    const target = new V(LANE_X + nav.pan.x, roadY[R0] + 0.9 + nav.pan.y, nav.pan.z);
    const cp = Math.cos(nav.pitch);
    const offset = new V(Math.sin(nav.yaw) * cp, Math.sin(nav.pitch), Math.cos(nav.yaw) * cp).multiplyScalar(nav.dist);
    camera.position.copy(target).add(offset);
    camera.lookAt(target);

    renderer.render(scene, camera);

    frames++;
    const elapsed = nowS - fpsT0;
    if (elapsed >= 1) { onFps(Math.round(frames / elapsed)); frames = 0; fpsT0 = nowS; }
  }

  requestAnimationFrame(tick);

  return {
    resetView,
    setGhost(on) { ghostOn = !!on; if (!ghostOn) clearGhosts(); },
    setGhostMode(m) { ghostMode = m; clearGhosts(); spawnIn = 2; },
    setWorkout(workout) {
      profile = workout ? workout.gradient_per_second : null;
      // the ghost animates on its virtual road distance and speed
      ghostDistance = workout && workout.ghost_virtual_distance_m ? workout.ghost_virtual_distance_m : null;
      ghostSpeedKmh = workout && workout.ghost_virtual_speed_kmh ? workout.ghost_virtual_speed_kmh : null;
      ghostCadenceRpm = workout && workout.ghost_virtual_cadence_rpm ? workout.ghost_virtual_cadence_rpm : null;
      ghostLevels = workout && workout.ghost_levels ? workout.ghost_levels : null;
      clearGhosts();
    },
    setSnapshot(s) {
      snap = s;
      snapAt = performance.now() / 1000;
      // keep the last minute of your virtual speed for the adaptive ghosts
      speedHist.push({ t: snapAt, v: (s.virtual_speed_kmh || 0) / 3.6 });
      while (speedHist.length && speedHist[0].t < snapAt - 60) speedHist.shift();
      if (s.status === "idle") clearGhosts();   // a new ride starts with no ghosts on the road
      if (s.status === "paused") snap = { ...s, speed_kmh: 0, cadence_rpm: 0 };
    },
    dispose() { renderer.dispose(); },
  };
}
