// Rowing world: the river is a ribbon that bends through corners instead of climbing gradients.
// The scull stays at the origin; the river, banks and everything on them are placed from the
// distance travelled, like the ride world's road. Flat colours, seeded randomness.
import * as THREE from "/static/vendor/three.module.min.js";

const HALF_W = 14;          // river half-width (m)
const BANK_W = 70;          // bank width beyond the river (m)
const D_BACK = 60;          // scenery kept behind the boat (m)
const D_AHEAD = 340;        // scenery kept ahead of the boat (m)
const ROW_STEP = 1;         // metres between river rows
const CENTRE_LEN = 20000;   // metres of centreline precomputed
const NODE_EVERY = 250;     // a heading node every 250 m; corners sit between nodes
const CORNER_M = 70;        // length of a corner (heading transition)
const TREE_CELL = 30;
const TREE_MAX = 320;
const SAIL_PERIOD = 900;
const DUCK_PERIOD = 600;   // one flock, about every 600 m
const BRIDGE_PERIOD = 1500;
const BALLOON_PERIOD = 1470;
const AIRSHIP_PERIOD = 1260;

const hash = (n) => { const s = Math.sin(n * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s); };
const wrapRel = (d, dNow, back, period) => ((((d - dNow + back) % period) + period) % period) - back;
const smoothstep = (x) => { const t = Math.min(1, Math.max(0, x)); return t * t * (3 - 2 * t); };

// ---------- the river centreline ----------
// Heading (radians, 0 = straight away from the boat). Straights and corners alternate;
// each corner is a smooth turn of CORNER_M metres between two straights.
const NODE_H = [];
for (let k = 0; k < Math.ceil(CENTRE_LEN / NODE_EVERY) + 2; k++) {
  const sign = hash(k + 900) < 0.5 ? -1 : 1;
  NODE_H[k] = sign * (0.35 + hash(k + 910) * 0.6) * (hash(k + 920) < 0.25 ? 0 : 1);
}
function headingAt(a) {
  const k = Math.floor(a / NODE_EVERY);
  const within = a - k * NODE_EVERY;
  const h0 = NODE_H[k], h1 = NODE_H[k + 1];
  const start = NODE_EVERY - CORNER_M;
  if (within <= start) return h0;
  return h0 + (h1 - h0) * smoothstep((within - start) / CORNER_M);
}
// Centreline in absolute coordinates, forward = (sin h, -cos h), one point per metre.
const CX = new Float64Array(CENTRE_LEN + 1);
const CZ = new Float64Array(CENTRE_LEN + 1);
{
  let x = 0, z = 0;
  for (let a = 0; a <= CENTRE_LEN; a++) {
    CX[a] = x; CZ[a] = z;
    const h = headingAt(a);
    x += Math.sin(h); z += -Math.cos(h);
  }
}
const clampA = (a) => Math.min(CENTRE_LEN - 1, Math.max(0, a));
// Absolute point at distance a, interpolated, and the river's normal and heading there.
function centreAt(a) {
  const c = clampA(a), i = Math.floor(c), f = c - i;
  // heading is taken at the exact distance, not at the whole metre, so bends turn smoothly
  return { x: CX[i] + (CX[i + 1] - CX[i]) * f, z: CZ[i] + (CZ[i + 1] - CZ[i]) * f, h: headingAt(c) };
}
// Boat frame: the scull at the origin, heading -z. Rotate absolute points by the boat's heading.
function toBoatFrame(x, z, h0) {
  const c = Math.cos(h0), s = Math.sin(h0);
  return [x * c + z * s, -x * s + z * c];
}
// A point at distance a (absolute), lateral offset lat (m, + = right bank), in the boat frame.
function riverPoint(a, lat, dNow) {
  const here = centreAt(dNow);
  const p = centreAt(a);
  const nx = Math.cos(p.h), nz = Math.sin(p.h);           // normal to the river at a
  const [bx, bz] = toBoatFrame(p.x + nx * lat - here.x, p.z + nz * lat - here.z, here.h);
  return [bx, bz];
}

// ---------- palette ----------
const COLORS = {
  water: 0x3a9cc4, bank: 0x9ed36a, bankEdge: 0x5a9a43, trunk: 0x6b4f32, pine: 0x2e6b3a, broad: 0x5aa04a,
  reed: 0x9bb35a, cloud: 0xffffff, boathouse: 0xf5efe0, roofRed: 0xb03a2e, swan: 0xfbfbf8, beak: 0xf08c2a,
  wake: 0xffffff,
  hull: 0xe9edf1, hullTrim: 0xc2410c, wood: 0xd9c08a, kit: 0x1f3a8a, skin: 0xf0c8a0, hair: 0x3b2a1e,
  blade: 0xf2f2f2, sail: 0xfafafa, duck: 0x7a5a3a, duckHead: 0x2d6b3c, bridge: 0x8b8f96,
  blondeHair: 0xe8c872,
  sky: 0x9fd8f4, seat: 0x2b2b2b, dolphin: 0x6d8ba3, dolphinBelly: 0xdfe8ef, bark: 0x6b5340,
  rope: 0x4a3a2a, envelope: [0xd9d2c3, 0xb9c4cf, 0xe0c9a6],
  balloon: [0xd7263d, 0xf4a300, 0x2e86ab, 0x7bc043, 0xf2f2f2, 0x8e44ad],
};

// Unit cylinder placed between two points (limbs, oars, struts, ropes).
const _up = new THREE.Vector3(0, 1, 0);
const _dir = new THREE.Vector3();

// Two-bone leg solve: the knee sits on the circle of radius L1/L2 around hip and foot,
// bent upwards (away from the boat's floor). Keeps knees bent, never overstretches.
const LEG = 0.435;          // thigh and shin of a 1.8 m rower (about 0.44 m each)
// Share of the stroke spent in the drive. The recovery gets the rest, so it runs 1.5x faster.
const DRIVE = 0.6;

// Blade tip for one oar, in boat coordinates. Drive (0-0.4): in the water, sweeping from the
// catch at the bow to the finish. Release (0.4-0.5): still in the water, a short push towards
// the bow. Recovery (0.5-0.9): lifted clear and returned to the catch. Back in the water 0.9-0.97.
// The oar swings about the oarlock on the gunwale. The blade angle is measured from the
// boat's lateral axis, positive towards the stern: about 20 degrees towards the bow at the
// catch (knees bent) and about 40 degrees towards the stern at the finish (knees straight).
function oarTip(side, p, reach) {
  const D = Math.PI / 180;
  const lerp = (a, b, t) => a + (b - a) * t;
  let th;
  if (p < DRIVE) th = lerp(-30 * D, 60 * D, smoothstep(p / DRIVE));                        // drive
  else if (p < DRIVE + 0.06) th = lerp(60 * D, 55 * D, smoothstep((p - DRIVE) / 0.06));   // release: a little towards the bow
  else th = lerp(55 * D, -30 * D, smoothstep((p - DRIVE - 0.06) / (1 - DRIVE - 0.06)));   // recovery in the air
  const lift = smoothstep((p - DRIVE - 0.02) / 0.08) * (1 - smoothstep((p - 0.92) / 0.06));
  const R = 1.6, lockX = 1.0;
  return new THREE.Vector3(side * (lockX + R * Math.cos(th)), -0.11 + 0.4 * lift, LOCK_Z + R * Math.sin(th));
}

// The rower faces the stern (+z). The footplate is in front of the rower, on the stern side.
const STRETCHER_Z = -0.6;   // footplate position along the boat (positive = stern), 2 m towards the bow
const SEAT_CATCH = -1.2;    // seat at the catch: knees well bent (0.6 m from the footplate)
const SEAT_FINISH = -1.6;   // seat at the finish: legs straight (1.0 m from the footplate)
const LOCK_Z = (SEAT_CATCH + SEAT_FINISH) / 2;   // oarlocks sit level with the middle of the seat's travel
// Two-bone solve for an arbitrary joint: the middle joint lies on the circle of radius L1/L2
// around the two ends, on the side the hint points to. Used for the elbows.
function jointFrom(a, c, L1, L2, hint) {
  const u = new THREE.Vector3().subVectors(c, a);
  const d = Math.min(u.length(), L1 + L2 - 0.02);
  u.normalize();
  const a1 = (L1 * L1 - L2 * L2 + d * d) / (2 * d);
  const h = Math.sqrt(Math.max(0, L1 * L1 - a1 * a1));
  const base = a.clone().addScaledVector(u, a1);
  const perp = hint.clone().addScaledVector(u, -hint.dot(u));
  if (perp.lengthSq() < 1e-6) perp.set(0, -1, 0);
  return base.addScaledVector(perp.normalize(), h);
}

function kneeFrom(hip, foot) {
  const u = new THREE.Vector3().subVectors(foot, hip);
  let d = u.length();
  d = Math.min(d, 2 * LEG - 0.02);
  u.normalize();
  const a = d / 2;                                  // equal bones: knee is centred on the chord
  const h = Math.sqrt(Math.max(0, LEG * LEG - a * a));
  const base = hip.clone().addScaledVector(u, a);
  const up = new THREE.Vector3(0, 1, 0);
  const perp = up.clone().addScaledVector(u, -up.dot(u));
  if (perp.lengthSq() < 1e-6) perp.set(1, 0, 0);
  return base.addScaledVector(perp.normalize(), h);
}
function beam(mesh, a, b) {
  _dir.subVectors(b, a);
  const len = _dir.length() || 1e-6;
  mesh.position.addVectors(a, b).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(_up, _dir.normalize());
  mesh.scale.set(1, len, 1);
}

export function createRowWorld(container, onFps) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setClearColor(COLORS.sky);
  renderer.shadowMap.enabled = false;   // no shadows, as in the ride app
  renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
  container.prepend(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(COLORS.sky);
  scene.fog = new THREE.Fog(COLORS.sky, 140, 460);
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 1200);
  // sunlight from the upper left, casting shadows around the boat (the scene is rider-relative,
  // so the shadow box stays centred on the scull)
  const sun = new THREE.DirectionalLight(0xfff1d6, 1.6);
  sun.position.set(-30, 60, -20);
  scene.add(sun);
  scene.add(sun.target);
  scene.add(new THREE.HemisphereLight(0xcfe8ff, 0x5a6b3a, 0.9));

  const lam = (color, extra = {}) => new THREE.MeshLambertMaterial({ color, ...extra });
  const dummy = new THREE.Object3D();

  // ---------- river and banks: ribbons rebuilt every frame ----------
  const ROWS = Math.round((D_BACK + D_AHEAD) / ROW_STEP) + 1;
  const riverGeo = new THREE.BufferGeometry();
  const riverPos = new Float32Array(ROWS * 2 * 3);
  const riverIdx = [];
  for (let r = 0; r < ROWS - 1; r++) {
    const a = r * 2, b = a + 1, c = a + 2, d = a + 3;
    riverIdx.push(a, b, c, b, d, c);
  }
  riverGeo.setIndex(riverIdx);
  riverGeo.setAttribute("position", new THREE.BufferAttribute(riverPos, 3));
  const river = new THREE.Mesh(riverGeo, lam(COLORS.water, { side: THREE.DoubleSide }));
  river.frustumCulled = false;
  river.receiveShadow = true;
  scene.add(river);

  const bankPos = new Float32Array(ROWS * 4 * 3);
  const bankIdx = [];
  for (let r = 0; r < ROWS - 1; r++) {
    for (const side of [0, 1]) {
      const o = r * 4 + side * 2;             // [inner, outer] per side, per row
      const i0 = o, i1 = o + 1, i2 = o + 4, i3 = o + 5;
      bankIdx.push(i0, i1, i2, i1, i3, i2);
    }
  }
  const bankGeo = new THREE.BufferGeometry();
  bankGeo.setIndex(bankIdx);
  bankGeo.setAttribute("position", new THREE.BufferAttribute(bankPos, 3));
  const banks = new THREE.Mesh(bankGeo, lam(COLORS.bank, { side: THREE.DoubleSide }));
  banks.frustumCulled = false;
  banks.receiveShadow = true;
  scene.add(banks);

  // ---------- trees ----------
  const trunks = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.25, 0.35, 2.4, 12), lam(COLORS.trunk), TREE_MAX);
  const pines = new THREE.InstancedMesh(new THREE.ConeGeometry(1.6, 5.5, 14), lam(COLORS.pine), TREE_MAX);
  const broads = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(2.1, 2), lam(COLORS.broad), TREE_MAX);
  for (const im of [trunks, pines, broads]) { im.frustumCulled = false; im.castShadow = true; im.receiveShadow = true; scene.add(im); }

  // ---------- bridges ----------
  const bridges = [];
  for (let i = 0; i < 3; i++) {
    const g = new THREE.Group();
    const deck = new THREE.Mesh(new THREE.BoxGeometry(HALF_W * 2 + 10, 0.5, 4), lam(COLORS.bridge));
    deck.position.y = 4;
    g.add(deck);
    for (const x of [-HALF_W - 4, HALF_W + 4]) {
      const post = new THREE.Mesh(new THREE.BoxGeometry(0.8, 4, 0.8), lam(COLORS.bridge));
      post.position.set(x, 2, 0);
      g.add(post);
    }
    scene.add(g);
    bridges.push({ g, d: 500 + i * BRIDGE_PERIOD });
  }

  // ---------- sailing boats ----------
  const sailBoats = [];
  for (let i = 0; i < 4; i++) {
    const g = new THREE.Group();
    const hull = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.5, 4), lam(COLORS.hullTrim));
    hull.position.y = 0.25;
    g.add(hull);
    const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 6, 10), lam(COLORS.hull));
    mast.position.y = 3.2;
    g.add(mast);
    const shape = new THREE.Shape();
    shape.moveTo(0, 0); shape.lineTo(0, 5.5); shape.lineTo(2.2, 0.3); shape.lineTo(0, 0);
    const sail = new THREE.Mesh(new THREE.ShapeGeometry(shape), lam(COLORS.sail, { side: THREE.DoubleSide }));
    sail.position.set(0.05, 0.6, 0);
    sail.rotation.y = Math.PI / 2;
    g.add(sail);
    scene.add(g);
    sailBoats.push({ g, d: 200 + i * (SAIL_PERIOD / 4), lat: (i % 2 ? 1 : -1) * (5 + hash(i) * 4) });
  }

  // ---------- ducks ----------
  const ducks = [];
  // one flock of mallards that swims together, rather than single ducks
  const FLOCK_SIZE = 6;
  const flock = new THREE.Group();
  scene.add(flock);
  for (let i = 0; i < FLOCK_SIZE; i++) {
    const g = new THREE.Group();
    // a mallard: brown body, white chest, green head and neck, yellow bill, short tail
    const body = new THREE.Mesh(new THREE.SphereGeometry(0.4, 24, 16), lam(COLORS.duck));
    body.scale.set(1.0, 0.55, 1.5);
    body.position.y = 0.22;
    g.add(body);
    const chest = new THREE.Mesh(new THREE.SphereGeometry(0.24, 20, 16), lam(0xf4f1e8));
    chest.scale.set(1.0, 0.8, 0.9);
    chest.position.set(0, 0.2, -0.4);
    g.add(chest);
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.13, 0.4, 16), lam(COLORS.duckHead));
    neck.position.set(0, 0.5, -0.45);
    neck.rotation.x = 0.5;
    g.add(neck);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.17, 24, 16), lam(COLORS.duckHead));
    head.position.set(0, 0.7, -0.6);
    g.add(head);
    const bill = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.06, 0.26), lam(0xf2b705));
    bill.position.set(0, 0.66, -0.85);
    g.add(bill);
    const tail = new THREE.Mesh(new THREE.ConeGeometry(0.12, 0.3, 12), lam(0x5a4530));
    tail.position.set(0, 0.3, 0.6);
    tail.rotation.x = Math.PI / 2 + 0.5;
    g.add(tail);
    g.position.set((hash(i + 60) - 0.5) * 3.5, 0, (hash(i + 70) - 0.5) * 3.5);   // loose formation
    flock.add(g);
  }
  ducks.push({ g: flock, d: 120, lat: -(HALF_W - 3) });

  // ---------- hot-air balloons and airships (as in the ride world) ----------
  const ropeMat = lam(COLORS.rope);
  const balloons = [];
  for (let i = 0; i < 7; i++) {
    const g = new THREE.Group();
    const env = new THREE.Mesh(new THREE.SphereGeometry(5, 36, 24), lam(COLORS.balloon[i % COLORS.balloon.length], { flatShading: true }));
    env.scale.set(1, 1.2, 1);
    g.add(env);
    const basket = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.1, 1.4), lam(COLORS.bark));
    basket.position.y = -7.6;
    g.add(basket);
    for (const [sx, sz] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const rope = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 1, 8), ropeMat);
      beam(rope, new THREE.Vector3(sx * 0.7, -7.0, sz * 0.7), new THREE.Vector3(sx * 2.2, -3.2, sz * 2.2));
      g.add(rope);
    }
    scene.add(g);
    balloons.push({ g, d: 260 + i * 210 + hash(i + 400) * 80, lat: (i % 2 ? 1 : -1) * (30 + hash(i + 410) * 60),
      y: 60 + hash(i + 420) * 50, phase: hash(i + 430) * 6 });
  }

  const envMats = COLORS.envelope.map((c) => lam(c, { flatShading: true }));
  const airships = [];
  for (let i = 0; i < 3; i++) {
    const g = new THREE.Group();
    const env = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 32), envMats[i]);
    env.scale.set(14, 4.5, 4.5);
    g.add(env);
    const finH = new THREE.Mesh(new THREE.BoxGeometry(4.5, 0.15, 5), lam(COLORS.bark));
    finH.position.set(-12, 0, 0);
    g.add(finH);
    const finV = new THREE.Mesh(new THREE.BoxGeometry(4.5, 5, 0.15), lam(COLORS.bark));
    finV.position.set(-12, 0, 0);
    g.add(finV);
    const gondola = new THREE.Mesh(new THREE.BoxGeometry(5, 1.6, 1.6), lam(COLORS.bark));
    gondola.position.set(0, -6.2, 0);
    g.add(gondola);
    scene.add(g);
    airships.push({ g, d: 400 + i * 420 + hash(i + 600) * 120, lat: (i % 2 ? 1 : -1) * (40 + hash(i + 610) * 60),
      y: 45 + hash(i + 620) * 18, phase: hash(i + 630) * 6 });
  }

  // ---------- dolphins: the other riders, swimming and leaping alongside the river ----------
  const dolphinGeo = new THREE.SphereGeometry(0.5, 24, 16);
  const dolphinPool = [];
  const dolphinGroup = new THREE.Group();
  scene.add(dolphinGroup);
  function makeDolphin() {
    const g = new THREE.Group();
    const body = new THREE.Mesh(dolphinGeo, lam(COLORS.dolphin));
    body.scale.set(0.55, 0.5, 1.6);
    g.add(body);
    const belly = new THREE.Mesh(dolphinGeo, lam(COLORS.dolphinBelly));
    belly.scale.set(0.5, 0.2, 1.45);
    belly.position.y = -0.16;
    g.add(belly);
    const fin = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.45, 0.35), lam(COLORS.dolphin));
    fin.position.set(0, 0.45, 0.2);
    g.add(fin);
    const tail = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.06, 0.35), lam(COLORS.dolphin));
    tail.position.set(0, 0.0, 1.05);
    g.add(tail);
    g.visible = false;
    dolphinGroup.add(g);
    return { g, id: null };
  }

  // ---------- sunny scenery: clouds, reeds, boathouses, swans, the wake ----------
  const cloudMat = lam(COLORS.cloud, { transparent: true, opacity: 0.95 });
  const clouds = [];
  for (let i = 0; i < 12; i++) {
    const g = new THREE.Group();
    const n = 4 + Math.floor(hash(i + 700) * 3);
    for (let j = 0; j < n; j++) {
      const puff = new THREE.Mesh(new THREE.SphereGeometry(4 + hash(i * 9 + j) * 3, 24, 16), cloudMat);
      puff.position.set((j - n / 2) * 4.2, hash(i * 13 + j) * 2, hash(i * 7 + j) * 3);
      g.add(puff);
    }
    scene.add(g);
    clouds.push({ g, d: i * 170 + hash(i + 710) * 60, lat: (hash(i + 720) - 0.5) * 260, y: 60 + hash(i + 730) * 40, phase: hash(i + 740) * 6 });
  }

  const REED_MAX = 160;
  const reeds = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.05, 0.05, 1.6, 8), lam(COLORS.reed), REED_MAX);
  reeds.frustumCulled = false;
  scene.add(reeds);

  // A boathouse: timber building with a gabled roof, an open boat bay facing the river,
  // windows, a wooden jetty and a boat on a rack. The long side runs along the river.
  function makeBoathouse(variant) {
    const g = new THREE.Group();
    const timber = lam(variant % 2 ? 0xa9825a : 0x8c6a48);
    const trim = lam(COLORS.boathouse);
    const slate = lam(COLORS.roofRed);
    const bayDark = lam(0x2b3a44);
    const glass = lam(0x9fd3e6, { emissive: 0x1d3442 });

    // walls: 7 m deep (across the river), 12 m long (along it), 4 m high
    const body = new THREE.Mesh(new THREE.BoxGeometry(7, 4, 12), timber);
    body.position.y = 2;
    g.add(body);
    // gable ends, a triangle in the x-y plane at each end of the building
    const gableShape = new THREE.Shape();
    gableShape.moveTo(-3.5, 0); gableShape.lineTo(3.5, 0); gableShape.lineTo(0, 2.5); gableShape.lineTo(-3.5, 0);
    const gableGeo = new THREE.ExtrudeGeometry(gableShape, { depth: 0.2, bevelEnabled: false });
    for (const z of [-6.1, 5.9]) {
      const gable = new THREE.Mesh(gableGeo, timber);
      gable.position.set(0, 4, z);
      g.add(gable);
    }
    // gabled roof: two sloping slabs meeting at the ridge, with a red tile colour
    const slope = Math.atan2(2.5, 3.5);
    const roofLen = Math.hypot(3.5, 2.5) + 0.3;
    for (const s of [-1, 1]) {
      const roof = new THREE.Mesh(new THREE.BoxGeometry(roofLen, 0.22, 12.8), slate);
      roof.position.set(s * 1.75, 5.25, 0);
      roof.rotation.z = -s * slope;
      g.add(roof);
    }
    // ridge beam
    const ridge = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.25, 12.8), trim);
    ridge.position.set(0, 6.5, 0);
    g.add(ridge);

    // the boat bay on the river side (+x): a dark opening with white slats
    const bay = new THREE.Mesh(new THREE.BoxGeometry(0.1, 2.8, 8), bayDark);
    bay.position.set(3.52, 1.4, 0);
    g.add(bay);
    for (let k = 0; k < 5; k++) {
      const slat = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.12, 8.1), trim);
      slat.position.set(3.55, 0.3 + k * 0.6, 0);
      g.add(slat);
    }
    // windows on the river side, either side of the bay, with frames
    for (const z of [-4.2, 4.2]) {
      const pane = new THREE.Mesh(new THREE.BoxGeometry(0.1, 1.0, 1.2), glass);
      pane.position.set(3.52, 2.9, z);
      g.add(pane);
      const frame = new THREE.Mesh(new THREE.BoxGeometry(0.12, 1.14, 1.34), trim);
      frame.position.set(3.5, 2.9, z);
      g.add(frame);
    }

    // wooden jetty out to the water, on posts
    for (const z of [-1.4, 1.4]) {
      const plank = new THREE.Mesh(new THREE.BoxGeometry(6.5, 0.16, 1.1), lam(0x9b7a52));
      plank.position.set(6.6, 0.6, z);
      g.add(plank);
    }
    for (let k = 0; k < 3; k++) {
      const post = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 1.4, 10), timber);
      post.position.set(5 + k * 2.2, -0.1, -1.6);
      g.add(post);
      const post2 = post.clone();
      post2.position.z = 1.6;
      g.add(post2);
    }

    // a racing boat on a rack beside the bay
    const boat = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.16, 7.5), lam(COLORS.hullTrim));
    boat.position.set(4.7, 2.0, 0);
    g.add(boat);
    for (const z of [-2.5, 2.5]) {
      const stand = new THREE.Mesh(new THREE.BoxGeometry(0.16, 1.9, 0.16), timber);
      stand.position.set(4.7, 1.05, z);
      g.add(stand);
    }
    return g;
  }

  const boathouses = [];
  for (let i = 0; i < 4; i++) {
    const g = makeBoathouse(i);
    scene.add(g);
    boathouses.push({ g, d: 300 + i * 420, lat: -(HALF_W + 10) });
  }

  // an open-air swimming pool on the right bank, 100 m past the start. Its long side runs along the river.
  function makePool() {
    const g = new THREE.Group();
    const deck = new THREE.Mesh(new THREE.BoxGeometry(16, 0.3, 32), lam(0xd9d4c7));
    deck.position.y = 0.15;
    g.add(deck);
    const water = new THREE.Mesh(new THREE.BoxGeometry(12, 0.2, 25), lam(0x3fc1e0, { emissive: 0x0a3a4a }));
    water.position.set(0, 0.25, 0);   // top at 0.35, clear of the deck top (0.30): no z-fighting
    g.add(water);
    const rimMat = lam(0xffffff);
    for (const [x, z, w, d] of [[0, 12.8, 12.6, 0.6], [0, -12.8, 12.6, 0.6], [6.3, 0, 0.6, 25.6], [-6.3, 0, 0.6, 25.6]]) {
      const rim = new THREE.Mesh(new THREE.BoxGeometry(w, 0.14, d), rimMat);
      rim.position.set(x, 0.37, z);
      g.add(rim);
    }
    // lane ropes across the pool, alternating red and white
    for (let k = 0; k < 5; k++) {
      const rope = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.05, 25), lam(k % 2 ? 0xffffff : 0xe03a3a));
      rope.position.set(-4.8 + k * 2.4, 0.42, 0);
      g.add(rope);
    }
    // diving board on a post at the far end
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.4, 1.4, 0.4), lam(0xb8b2a4));
    post.position.set(0, 0.7, -15.2);
    g.add(post);
    const board = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.12, 3.0), lam(0xe8e4da));
    board.position.set(0, 1.45, -16.2);
    g.add(board);
    // changing cabins and umbrellas on the deck
    for (const z of [7, -6]) {
      const cabin = new THREE.Mesh(new THREE.BoxGeometry(4, 2.6, 3), lam(0xf5f1e6));
      cabin.position.set(-14, 1.3, z);
      g.add(cabin);
      const cabinRoof = new THREE.Mesh(new THREE.BoxGeometry(4.3, 0.2, 3.3), lam(0x2b6cb0));
      cabinRoof.position.set(-14, 2.7, z);
      g.add(cabinRoof);
    }
    // lifeguard chair
    for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.12, 2.4, 0.12), lam(0xb8b2a4));
      leg.position.set(-2 + x * 0.6, 1.2, 9 + z * 0.6);
      g.add(leg);
    }
    const chairSeat = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.2, 1.5), lam(0xe03a3a));
    chairSeat.position.set(-2, 2.5, 9);
    g.add(chairSeat);

    return g;
  }
  const swimmingPool = { g: makePool(), d: 100, lat: HALF_W + 9 };   // the deck's inner edge clears the river
  scene.add(swimmingPool.g);

  // Grass strips along the bank, before and after the pool (the pool deck spans 84-116 m).
  // Each strip has towels and umbrellas; the one after the pool has the ice kiosk.
  const LAWN_LAT = HALF_W + 4;     // 4 m out from the water's edge
  function makeLawnStrip(len, withKiosk, seed) {
    const g = new THREE.Group();
    const lawn = new THREE.Mesh(new THREE.BoxGeometry(6, 0.12, len), lam(0x7cc45a));
    lawn.position.set(0, 0.06, 0);
    g.add(lawn);
    const towelColors = [0xe03a3a, 0x2b6cb0, 0xf2c200, 0x2e9e6a, 0xffffff, 0xe9739c];
    const towels = Math.round(len / 4);
    for (let k = 0; k < towels; k++) {
      const towel = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.03, 0.7), lam(towelColors[k % towelColors.length]));
      towel.position.set((hash(seed + k * 7 + 1) - 0.5) * 4.2, 0.14, -len / 2 + 2 + hash(seed + k * 7 + 2) * (len - 4));
      towel.rotation.y = hash(seed + k * 7 + 3) * 0.6;
      g.add(towel);
    }
    const umbrellas = Math.max(1, Math.round(len / 22));
    const umbrellaColors = [0xe03a3a, 0xf2c200, 0x2e9e6a, 0x2b6cb0];
    for (let k = 0; k < umbrellas; k++) {
      const z = -len / 2 + (k + 0.5) * (len / umbrellas);
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 2.2, 8), lam(0xffffff));
      pole.position.set(-1.2, 1.1, z);
      g.add(pole);
      const canopy = new THREE.Mesh(new THREE.ConeGeometry(1.9, 0.8, 16), lam(umbrellaColors[k % umbrellaColors.length]));
      canopy.position.set(-1.2, 2.4, z);
      g.add(canopy);
    }
    if (withKiosk) {
      // the ice kiosk: a white booth with a blue counter, a striped awning and a cone sign
      const kiosk = new THREE.Group();
      const booth = new THREE.Mesh(new THREE.BoxGeometry(3, 2.6, 2.4), lam(0xf7f7f2));
      booth.position.set(0, 1.3, 0);
      kiosk.add(booth);
      const counter = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.12, 0.5), lam(0x2b6cb0));
      counter.position.set(0, 1.0, 1.45);
      kiosk.add(counter);
      for (let k = 0; k < 6; k++) {
        const stripe = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.16, 1.9), lam(k % 2 ? 0xffffff : 0xe03a3a));
        stripe.position.set(-1.5 + 0.6 + k * 0.5, 2.75, 0.6);
        kiosk.add(stripe);
      }
      const sign = new THREE.Mesh(new THREE.ConeGeometry(0.5, 1.0, 12), lam(0xf2c200));
      sign.position.set(0, 3.5, 0);
      kiosk.add(sign);
      kiosk.position.set(0, 0, len / 2 - 6);
      g.add(kiosk);
    }
    return g;
  }
  const lawnStrips = [
    { g: makeLawnStrip(44, false, 300), d: 62, len: 44 },    // before the pool: 40-84 m
    { g: makeLawnStrip(44, true, 500), d: 138, len: 44 },    // after the pool: 116-160 m, with the kiosk
  ];
  for (const s of lawnStrips) scene.add(s.g);

  const swans = [];
  for (let i = 0; i < 3; i++) {
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.SphereGeometry(0.7, 24, 16), lam(COLORS.swan));
    body.scale.set(0.7, 0.45, 1.3);
    body.position.y = 0.3;
    g.add(body);
    const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.12, 0.9, 12), lam(COLORS.swan));
    neck.position.set(0, 0.95, 0.7);
    neck.rotation.x = -0.5;
    g.add(neck);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.16, 16, 12), lam(COLORS.swan));
    head.position.set(0, 1.45, 0.98);
    g.add(head);
    const beak = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.08, 0.3), lam(COLORS.beak));
    beak.position.set(0, 1.4, 1.2);
    g.add(beak);
    scene.add(g);
    swans.push({ g, d: 150 + i * 230, lat: (i % 2 ? 1 : -1) * (HALF_W - 3), phase: hash(i + 800) * 6 });
  }

  // the wake: two white trailing arms behind the stern, lengthened with speed
  const wakeGeo = new THREE.BufferGeometry();
  wakeGeo.setAttribute("position", new THREE.Float32BufferAttribute([
    0, 0.05, 0.5,  -1.8, 0.05, 9,  -0.3, 0.05, 9,
    0, 0.05, 0.5,   0.3, 0.05, 9,   1.8, 0.05, 9,
  ], 3));
  const wake = new THREE.Mesh(wakeGeo, new THREE.MeshBasicMaterial({ color: COLORS.wake, transparent: true, opacity: 0.55, depthWrite: false }));
  scene.add(wake);   // the boat sits at the origin, so the wake goes straight into the scene

  function placeExtras(dNow, t) {
    for (const c of clouds) {
      const rel = wrapRel(c.d, dNow, D_BACK, 1200);
      c.g.visible = rel >= -D_BACK && rel <= D_AHEAD;
      if (c.g.visible) c.g.position.set(c.lat + Math.sin(t * 0.05 + c.phase) * 8, c.y, -rel);
    }
    let ri = 0;
    const k0 = Math.floor((dNow - D_BACK) / 8), k1 = Math.floor((dNow + D_AHEAD) / 8);
    const hidden = () => { dummy.position.set(0, -10, 0); dummy.scale.setScalar(0); dummy.updateMatrix(); return dummy.matrix; };
    for (let k = k0; k <= k1 && ri < REED_MAX; k++) {
      const seed = k * 2;
      const dAbs = k * 8 + hash(seed + 1) * 8;
      const rel = dAbs - dNow;
      if (rel < -D_BACK || rel > D_AHEAD) continue;
      const side = hash(seed + 2) < 0.5 ? -1 : 1;
      const [x, z] = riverPoint(dAbs, side * (HALF_W + 0.5 + hash(seed + 3) * 1.8), dNow);
      const s = 0.7 + hash(seed + 4) * 0.8;
      dummy.position.set(x, 0.8 * s, z); dummy.scale.set(s, s, s); dummy.updateMatrix();
      reeds.setMatrixAt(ri++, dummy.matrix);
    }
    for (let i = ri; i < REED_MAX; i++) reeds.setMatrixAt(i, hidden());
    reeds.instanceMatrix.needsUpdate = true;

    // the lawn strips along the bank, before and after the pool
    for (const s of lawnStrips) {
      const rel = wrapRel(s.d, dNow, D_BACK, 100000);
      s.g.visible = rel >= -D_BACK - s.len / 2 && rel <= D_AHEAD;
      if (s.g.visible) {
        const [x, z] = riverPoint(dNow + rel, LAWN_LAT, dNow);
        s.g.position.set(x, 0, z);
        const [x2, z2] = riverPoint(dNow + rel + 1, LAWN_LAT, dNow);
        s.g.rotation.y = -Math.atan2(x2 - x, z2 - z);
      }
    }
    // the pool stays at its place on the course (its distance does not repeat)
    {
      const rel = wrapRel(swimmingPool.d, dNow, D_BACK, 100000);
      swimmingPool.g.visible = rel >= -D_BACK && rel <= D_AHEAD + 40;
      if (swimmingPool.g.visible) {
        const [x, z] = riverPoint(dNow + rel, swimmingPool.lat, dNow);
        swimmingPool.g.position.set(x, 0, z);
        const [x2, z2] = riverPoint(dNow + rel + 1, swimmingPool.lat, dNow);
        swimmingPool.g.rotation.y = -Math.atan2(x2 - x, z2 - z);
      }
    }
    for (const bh of boathouses) {
      const rel = wrapRel(bh.d, dNow, D_BACK, 1680);   // four boathouses, 420 m apart
      bh.g.visible = rel >= -D_BACK && rel <= D_AHEAD;
      if (bh.g.visible) {
        const [x, z] = riverPoint(dNow + rel, bh.lat, dNow);
        bh.g.position.set(x, 0, z);
        const [x2, z2] = riverPoint(dNow + rel + 1, bh.lat, dNow);
        bh.g.rotation.y = -Math.atan2(x2 - x, z2 - z);
      }
    }
    for (const sw of swans) {
      const rel = wrapRel(sw.d, dNow, D_BACK, 1000);
      sw.g.visible = rel >= -D_BACK && rel <= D_AHEAD;
      if (sw.g.visible) {
        const [x, z] = riverPoint(dNow + rel, sw.lat + Math.sin(t * 0.2 + sw.phase) * 0.8, dNow);
        sw.g.position.set(x, Math.sin(t * 1.5 + sw.phase) * 0.05, z);
        const [x2, z2] = riverPoint(dNow + rel + 1, sw.lat, dNow);
        sw.g.rotation.y = -Math.atan2(x2 - x, z2 - z);
      }
    }
  }

  // ---------- the scull and its rower ----------
  const boat = new THREE.Group();
  scene.add(boat);
  // The hull in plan view: a sharp bow (at -z, the direction of travel), a rounder stern (+z),
  // flared sides. Extruded 0.16 m deep, then laid flat so it sits on the water.
  const hs = new THREE.Shape();
  hs.moveTo(0, 4.1);                                        // bow point (becomes -z)
  hs.quadraticCurveTo(0.2, 2.8, 0.25, 0.6);
  hs.lineTo(0.22, -1.3);
  hs.lineTo(0, -2.0);                                       // stern: a sharp point, shortened by 2 m
  hs.lineTo(-0.22, -1.3);
  hs.lineTo(-0.25, 0.6);
  hs.quadraticCurveTo(-0.2, 2.8, 0, 4.1);
  const hullGeo = new THREE.ExtrudeGeometry(hs, { depth: 0.16, bevelEnabled: false });
  hullGeo.rotateX(-Math.PI / 2);
  const hull = new THREE.Mesh(hullGeo, new THREE.MeshLambertMaterial({ color: COLORS.hull, side: THREE.DoubleSide }));
  boat.add(hull);
  // a red sheer line along the top edge of the hull
  const sheerShape = new THREE.Shape();
  sheerShape.moveTo(0, 4.05);
  sheerShape.quadraticCurveTo(0.19, 2.8, 0.245, 0.6);
  sheerShape.lineTo(0.215, -1.3);
  sheerShape.lineTo(0, -2.0);
  sheerShape.lineTo(-0.215, -1.3);
  sheerShape.lineTo(-0.245, 0.6);
  sheerShape.quadraticCurveTo(-0.19, 2.8, 0, 4.05);
  const sheerGeo = new THREE.ExtrudeGeometry(sheerShape, { depth: 0.025, bevelEnabled: false });
  sheerGeo.rotateX(-Math.PI / 2);
  const sheer = new THREE.Mesh(sheerGeo, new THREE.MeshLambertMaterial({ color: COLORS.hullTrim, side: THREE.DoubleSide }));
  sheer.position.y = 0.16;
  boat.add(sheer);

  // a bow flag with a start number, on a short pole at the tip of the bow
  const flagCanvas = document.createElement("canvas");
  flagCanvas.width = 128;
  flagCanvas.height = 96;
  const fg = flagCanvas.getContext("2d");
  fg.fillStyle = "#ffffff";
  fg.fillRect(0, 0, 128, 96);
  fg.fillStyle = "#c2410c";
  fg.fillRect(0, 0, 128, 14);
  fg.fillStyle = "#16232b";
  fg.font = "bold 64px system-ui, sans-serif";
  fg.textAlign = "center";
  fg.textBaseline = "middle";
  fg.fillText("81", 64, 52);
  const flagTex = new THREE.CanvasTexture(flagCanvas);
  // pole, flag and number at a third of the first size
  const bowPole = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.5, 8), lam(COLORS.hull));
  bowPole.position.set(0, 0.41, -4.0);   // at the very tip of the bow
  boat.add(bowPole);
  const bowFlag = new THREE.Mesh(new THREE.PlaneGeometry(0.267, 0.2),
    new THREE.MeshBasicMaterial({ map: flagTex, side: THREE.DoubleSide }));
  bowFlag.position.set(0.133, 0.56, -4.0);
  boat.add(bowFlag);
  // the seat rails stay inside the hull: from the bow to the stern, not beyond either end
  const rails = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.06, 5.0), lam(COLORS.kit));
  rails.position.set(0, 0.12, -1.0);
  boat.add(rails);
  const stretcher = new THREE.Mesh(new THREE.BoxGeometry(0.46, 0.2, 0.06), lam(COLORS.kit));
  stretcher.position.set(0, 0.2, STRETCHER_Z);   // footplate at the bow end, where the feet go
  boat.add(stretcher);

  const rower = new THREE.Group();
  boat.add(rower);
  const seat = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.06, 0.42), lam(COLORS.seat));
  rower.add(seat);
  const torsoPivot = new THREE.Group();
  rower.add(torsoPivot);
  // torso: a square block, 0.55 m from hip to shoulder, 0.34 m across
  // torso: a square block, 0.55 m from hip to shoulder, 0.34 m across
  const torso = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.55, 0.22), lam(COLORS.kit));
  torso.position.y = 0.275;
  torsoPivot.add(torso);
  // neck and head: an oval head about 0.23 m high, 0.16 m wide
  const neckMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.07, 0.1, 16), lam(COLORS.skin));
  neckMesh.position.set(0, 0.6, -0.01);
  torsoPivot.add(neckMesh);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.115, 24, 20), lam(COLORS.skin));
  head.scale.set(0.85, 1.05, 0.95);
  head.position.set(0, 0.7, -0.02);
  torsoPivot.add(head);
  const hair = new THREE.Mesh(new THREE.SphereGeometry(0.135, 24, 20, 0, Math.PI * 2, 0, Math.PI * 0.5), lam(COLORS.blondeHair));
  hair.position.set(0, 0.715, -0.02);
  hair.scale.set(0.85, 1.05, 0.95);
  torsoPivot.add(hair);

  const sculls = [];
  for (const side of [-1, 1]) {
    const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.035, 1, 12), lam(COLORS.skin));
    const forearm = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.03, 1, 12), lam(COLORS.skin));
    const thigh = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 1, 12), lam(COLORS.kit));
    const shin = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 1, 12), lam(COLORS.kit));
    boat.add(arm, forearm, thigh, shin);   // limbs are placed in boat coordinates
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 1, 12), lam(COLORS.wood));
    // a flat paddle: long along the shaft, wide across it, thin in height; lies flat on the water
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.03, 0.3), lam(COLORS.blade));
    boat.add(shaft, blade);
    // the disturbance on the water around the blade: a ring lying flat on the surface
    const rippleGeo = new THREE.RingGeometry(0.18, 0.32, 32);
    rippleGeo.rotateX(-Math.PI / 2);
    const ripple = new THREE.Mesh(rippleGeo, new THREE.MeshBasicMaterial({
      color: 0xffffff, transparent: true, opacity: 0.5, depthWrite: false,
    }));
    ripple.visible = false;
    scene.add(ripple);
    // shoulder, hand and foot: small rounded shapes at the joints
    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.04, 12, 8), lam(COLORS.skin));
    hand.scale.set(1.2, 0.7, 1.0);
    const foot = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.05, 0.26), lam(COLORS.kit));
    boat.add(hand, foot);
    sculls.push({ side, arm, forearm, thigh, shin, shaft, blade, ripple, hand, foot });
  }

  // the scull, the rower and the other moving things cast and take shadows too
  boat.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });

  // ---------- state ----------
  let snap = null;
  let snapArrivalMs = 0;     // when the last server snapshot arrived, for the position prediction
  let atStart = false;       // true during the countdown: the view stays at the start position
  let dispD = 0, lastStrokes = null;
  let lastStrokeMs = 0, strokePeriodS = 2.0;
  let sceneT = 0;
  let raf = 0;
  let lastFrame = performance.now();
  let frames = 0, fpsT = performance.now();
  let botsOn = true;

  function fit() {
    const w = Math.max(1, container.clientWidth), h = Math.max(1, container.clientHeight);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(fit) : null;
  if (ro) ro.observe(container);
  fit();

  function rebuildRiver(dNow) {
    // rows from D_BACK behind to D_AHEAD ahead of the scull, two verts per row (left, right)
    for (let r = 0; r < ROWS; r++) {
      const rel = -D_BACK + r * ROW_STEP;
      const a = dNow + rel;
      const [lx, lz] = riverPoint(a, -HALF_W, dNow);
      const [rx, rz] = riverPoint(a, HALF_W, dNow);
      riverPos[r * 6 + 0] = lx; riverPos[r * 6 + 1] = 0.02; riverPos[r * 6 + 2] = lz;
      riverPos[r * 6 + 3] = rx; riverPos[r * 6 + 4] = 0.02; riverPos[r * 6 + 5] = rz;
      for (const side of [0, 1]) {
        const lat0 = side === 0 ? -HALF_W : HALF_W;
        const [ix, iz] = riverPoint(a, lat0, dNow);
        const [ox, oz] = riverPoint(a, lat0 + Math.sign(lat0) * BANK_W, dNow);
        const o = (r * 2 + side) * 6;
        bankPos[o + 0] = ix; bankPos[o + 1] = 0.01; bankPos[o + 2] = iz;
        bankPos[o + 3] = ox; bankPos[o + 4] = 0.01; bankPos[o + 5] = oz;
      }
    }
    riverGeo.attributes.position.needsUpdate = true;
    bankGeo.attributes.position.needsUpdate = true;
    riverGeo.computeVertexNormals();
    bankGeo.computeVertexNormals();   // without normals the lighting is black
  }

  function placeScenery(dNow) {
    // trees on both banks, cell-hashed so they recycle with distance
    let ti = 0;
    const hidden = () => { dummy.position.set(0, 0, 0); dummy.scale.setScalar(0); dummy.updateMatrix(); return dummy.matrix; };
    const k0 = Math.floor((dNow - D_BACK) / TREE_CELL), k1 = Math.floor((dNow + D_AHEAD) / TREE_CELL);
    for (let k = k0; k <= k1 && ti < TREE_MAX - 2; k++) {
      for (let j = 0; j < 3 && ti < TREE_MAX - 2; j++) {
        const seed = k * 3 + j;
        const dAbs = k * TREE_CELL + hash(seed * 3 + 1) * TREE_CELL;
        const rel = dAbs - dNow;
        if (rel < -D_BACK || rel > D_AHEAD) continue;
        const side = hash(seed * 3 + 2) < 0.5 ? -1 : 1;
        const lat = side * (HALF_W + BANK_W * 0.25 + hash(seed * 3 + 3) * BANK_W * 0.6);
        const [x, z] = riverPoint(dAbs, lat, dNow);
        const s = 0.8 + hash(seed * 3 + 4) * 0.9;
        const pine = hash(seed * 7 + 5) < 0.55;
        dummy.position.set(x, 1.2 * s, z); dummy.scale.setScalar(s); dummy.updateMatrix();
        trunks.setMatrixAt(ti, dummy.matrix);
        if (pine) {
          dummy.position.set(x, 4.5 * s, z); dummy.updateMatrix();
          pines.setMatrixAt(ti, dummy.matrix); broads.setMatrixAt(ti, hidden());
        } else {
          dummy.position.set(x, 3.6 * s, z); dummy.updateMatrix();
          broads.setMatrixAt(ti, dummy.matrix); pines.setMatrixAt(ti, hidden());
        }
        ti++;
      }
    }
    for (let i = ti; i < TREE_MAX; i++) {
      trunks.setMatrixAt(i, hidden()); pines.setMatrixAt(i, hidden()); broads.setMatrixAt(i, hidden());
    }
    trunks.instanceMatrix.needsUpdate = true;
    pines.instanceMatrix.needsUpdate = true;
    broads.instanceMatrix.needsUpdate = true;

    for (const b of bridges) {
      const rel = wrapRel(b.d, dNow, D_BACK, BRIDGE_PERIOD);
      b.g.visible = rel >= -D_BACK && rel <= D_AHEAD;
      if (b.g.visible) {
        const [x, z] = riverPoint(dNow + rel, 0, dNow);
        b.g.position.set(x, 0, z);
        b.g.rotation.y = -Math.atan2(riverPoint(dNow + rel + 1, 0, dNow)[0] - x,
                                     riverPoint(dNow + rel + 1, 0, dNow)[1] - z);
      }
    }
    for (const sb of sailBoats) {
      const rel = wrapRel(sb.d, dNow, D_BACK, SAIL_PERIOD);
      sb.g.visible = rel >= -D_BACK && rel <= D_AHEAD;
      if (sb.g.visible) {
        const [x, z] = riverPoint(dNow + rel, sb.lat, dNow);
        sb.g.position.set(x, 0, z);
        sb.g.rotation.y = Math.sin(sceneT / 4 + sb.d) * 0.12;
      }
    }
    for (const du of ducks) {
      const rel = wrapRel(du.d, dNow, D_BACK, DUCK_PERIOD);
      du.g.visible = rel >= -D_BACK && rel <= D_AHEAD;
      if (du.g.visible) {
        const [x, z] = riverPoint(dNow + rel, du.lat + Math.sin(sceneT / 1.5 + du.d) * 0.6, dNow);
        du.g.position.set(x, 0, z);
      }
    }
    for (const b of balloons) {
      const rel = wrapRel(b.d, dNow, 30, BALLOON_PERIOD);
      b.g.visible = rel >= -30 && rel <= 450;
      if (b.g.visible) {
        const [x, z] = riverPoint(dNow + rel, b.lat + Math.sin(sceneT * 0.1 + b.phase) * 3, dNow);
        b.g.position.set(x, b.y, z);
      }
    }
    for (const a of airships) {
      const rel = wrapRel(a.d, dNow, 60, AIRSHIP_PERIOD);
      a.g.visible = rel >= -60 && rel <= 500;
      if (a.g.visible) {
        const [x, z] = riverPoint(dNow + rel, a.lat + Math.sin(sceneT * 0.05 + a.phase) * 6, dNow);
        a.g.position.set(x, a.y, z);
        a.g.rotation.y = Math.atan2(riverPoint(dNow + rel + 1, 0, dNow)[0] - x,
                                    riverPoint(dNow + rel + 1, 0, dNow)[1] - z);
      }
    }
  }

  // Dolphins: one per server rider, keyed by the rider's id. The server sends positions about
  // five times a second, so between snapshots each dolphin keeps swimming at its own speed and
  // eases to the server's position: smooth movement instead of jumps.
  const dolphinById = new Map();
  function placeDolphins(dNow, t, dt) {
    const riders = botsOn && snap && snap.bots ? snap.bots : [];
    // ghost-rider prediction, as in the ride app: last server position plus speed times the time since it arrived
    const extra = Math.min(1, (performance.now() - snapArrivalMs) / 1000);
    const seen = new Set();
    for (const r of riders) {
      seen.add(r.id);
      let e = dolphinById.get(r.id);
      if (!e) {
        e = { dp: makeDolphin(), d: r.distance_m, lat: r.lane_x * 3.5, phase: hash(r.id + 5) * 6 };
        dolphinById.set(r.id, e);
      }
      // prediction: the server's position plus its speed times the time since it arrived.
      // The dolphin keeps its own motion every frame and blends towards that prediction
      // over a few frames, so a new snapshot never makes it jump.
      const v = (r.speed_kmh || 0) / 3.6;
      const target = r.distance_m + v * extra;
      e.d += v * dt;
      e.d += (target - e.d) * (1 - Math.exp(-dt * 6));
      e.lat = r.lane_x * 3.5;
      const rel = e.d - dNow;
      e.dp.g.visible = rel >= -D_BACK && rel <= D_AHEAD;
      if (!e.dp.g.visible) continue;
      const lat = e.lat + Math.sin(t * 0.7 + e.phase) * 0.8;
      const [x, z] = riverPoint(e.d, lat, dNow);
      const leap = Math.max(0, Math.sin(t * 3.2 + e.phase));
      e.dp.g.position.set(x, 0.15 + 1.1 * leap * leap, z);
      const [x2, z2] = riverPoint(e.d + 1, lat, dNow);
      // the model's nose points to -z: yaw it so the nose faces the direction of travel,
      // and pitch the nose up while it rises out of the water
      e.dp.g.rotation.order = "YXZ";
      e.dp.g.rotation.y = Math.atan2(-(x2 - x), -(z2 - z));
      e.dp.g.rotation.x = 0.5 * Math.cos(t * 3.2 + e.phase) * (leap > 0 ? 1 : 0);
    }
    for (const [id, e] of dolphinById) {
      if (!seen.has(id)) { dolphinGroup.remove(e.dp.g); dolphinById.delete(id); }
    }
  }

  // Stroke phase: 0 = catch, 0.4 = finish, 1 = next catch. Anchored to each stroke event.
  function strokePhase(nowMs) {
    if (!snap || !snap.stroke_rate_spm) return 0;
    const sinceS = (nowMs - lastStrokeMs) / 1000;
    if (sinceS > 2 * strokePeriodS) return 0;
    return (sinceS / strokePeriodS) % 1;
  }

  // the boat's speed through the stroke: quicker in the drive, slower in the recovery.
  // The two parts roughly cancel, so the average speed is unchanged.
  function surge(p) {
    return p < DRIVE ? 0.2 * Math.sin(Math.PI * p / DRIVE) : -0.12 * Math.sin(Math.PI * (p - DRIVE) / (1 - DRIVE));
  }

  function animateBoat(nowMs) {
    const p = strokePhase(nowMs);
    const drive = p < DRIVE;
    const travel = SEAT_FINISH - SEAT_CATCH;
    const seatZ = drive ? SEAT_CATCH + travel * smoothstep(p / DRIVE)
                        : SEAT_FINISH - travel * smoothstep((p - DRIVE) / (1 - DRIVE));
    const reach = (seatZ - SEAT_CATCH) / travel;      // 0 at the catch, 1 at the finish
    // knees bent (catch): lean forward to the stern; knees straight (finish): lean back towards the bow
    // catch: upper body at 0 degrees (upright); finish: about 20 degrees back towards the bow
    const lean = -0.7 * reach;                          // back bent 40 degrees from vertical at the finish

    rower.position.set(0, 0.2, seatZ);
    seat.position.set(0, 0.03, 0);
    torsoPivot.position.set(0, 0.14, 0);   // hip joint, inside the buttocks
    // the rower faces the stern: the upper body is turned 180 degrees about the vertical axis,
    // and the lean (about the x axis) still goes towards the bow at the catch
    torsoPivot.rotation.set(lean, Math.PI, 0);
    // the shoulders sit on the top of the torso: rower height (0.2) + hinge (0.14) + torso length (0.55)
    const shoulderY = 0.29 + 0.55 * Math.cos(lean), shoulderZ = seatZ + 0.55 * Math.sin(lean);   // 5 cm lower than the top of the torso
    // knees bent (catch): arms straight out in front of the rower, towards the stern;
    // knees straight (finish): hands in front of the breast
    // hands are placed from the shoulders: arms fully extended at the catch, breast at the finish
    const handZ = shoulderZ + 0.5 - 0.25 * reach, handY = 0.55 + 0.15 * reach;
    // hand spread through the stroke: wide at the catch and the finish, close in the middle of
    // the drive and of the recovery. Seen from above the hands trace a figure eight.
    const handSpread = p < DRIVE
      ? 0.5 + 0.5 * Math.cos(2 * Math.PI * p / (2 * 0.3))
      : 0.5 + 0.5 * Math.cos(2 * Math.PI * (p - DRIVE) / 0.4);   // reaching forward at the catch, to the chest at the finish
    for (const s of sculls) {
      const sx = s.side;
      const shoulderP = new THREE.Vector3(sx * 0.18, shoulderY, shoulderZ);   // shoulder joints about 0.38 m apart
      const handP = new THREE.Vector3(sx * (0.08 + 0.22 * handSpread), handY, handZ);   // hands together, or apart at the catch and finish
      // elbows: upper arm and forearm, elbow pointing down and out
      const elbow = jointFrom(shoulderP, handP, 0.32, 0.27, new THREE.Vector3(sx * 0.4, -1, 0));
      beam(s.arm, shoulderP, elbow);
      beam(s.forearm, elbow, handP);
      s.hand.position.copy(handP);
      s.foot.position.copy(new THREE.Vector3(sx * 0.12, 0.2, STRETCHER_Z));
      const hip = new THREE.Vector3(sx * 0.08, 0.3, seatZ);   // on the seat (rower height 0.2 + seat 0.03 + hip)
      const foot = new THREE.Vector3(sx * 0.12, 0.2, STRETCHER_Z);
      const knee = kneeFrom(hip, foot);
      beam(s.thigh, hip, knee);
      beam(s.shin, knee, foot);
      const tip = oarTip(sx, p, reach);
      beam(s.shaft, handP, tip);
      // blade: yaw follows the shaft; feathering is a single 90 degree tilt about the shaft
      // (flat when in the water, edge-on when lifted), never a full turn
      // in the water: blade vertical, facing the direction of travel (maximum drag);
      // lifted clear: blade flat (minimum drag)
      const feather = (1 - Math.min(1, Math.max(0, (tip.y + 0.11) / 0.4))) * (Math.PI / 2);
      // the blade's long axis lies exactly on the shaft (full 3D direction), so the angle between
      // oar and blade stays fixed; the feathering turns the blade about that axis
      const dirS = new THREE.Vector3().subVectors(tip, handP).normalize();
      const wUp = new THREE.Vector3(0, 1, 0).addScaledVector(dirS, -dirS.y).normalize();
      const wFlat = new THREE.Vector3().crossVectors(dirS, wUp).normalize();
      const w = wUp.clone().multiplyScalar(Math.cos(feather)).addScaledVector(wFlat, Math.sin(feather));
      const nrm = new THREE.Vector3().crossVectors(w, dirS);
      s.blade.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(dirS, nrm, w));
      s.blade.position.copy(tip);
      // water around a blade that is in the water: the ring widens through the drive and fades on the lift
      const inWater = tip.y < -0.05;
      s.ripple.visible = inWater;
      if (inWater) {
        s.ripple.position.set(tip.x, 0.02, tip.z);
        s.ripple.scale.setScalar(0.7 + 0.6 * Math.sin(Math.min(1, p / DRIVE) * Math.PI / 2));
        s.ripple.material.opacity = 0.5 * (1 - Math.max(0, (tip.y + 0.11) / 0.4));
      }
    }
    boat.rotation.z = 0.012 * Math.sin(p * Math.PI * 2);
    // the boat pitches and bobs with each stroke: the bow dips at the catch and lifts at the finish
    // the boat follows the rower's weight: when the centre of mass moves towards the bow, the bow
    // goes down (a negative pitch lowers the bow, which is at -z); towards the stern, the stern goes down
    // a short nod: the bow dips only when the seat and back are fully extended towards the bow,
    // at the end of the drive, and comes straight back up
    const nod = Math.exp(-Math.pow((reach - 1) / 0.12, 2));
    boat.rotation.x = -0.012 * nod;
    boat.position.y = 0;
  }

  // ---------- view navigation, as in the ride world ----------
  // drag: orbit · right-drag or shift-drag: pan · wheel: zoom · double-click: reset ·
  // arrows: orbit, +/-: zoom, WASD: pan, R: reset, C: toggle chase camera.
  const DEFAULT_NAV = { yaw: 0, pitch: 0.3, dist: 7 };
  const TARGET = new THREE.Vector3(0, 0.8, 0);
  const nav = { ...DEFAULT_NAV, pan: { x: 0, y: 0 }, chase: true };
  const drag = { active: false, button: 0, x: 0, y: 0, shift: false };
  const keys = new Set();
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const el = renderer.domElement;
  el.style.touchAction = "none";

  function resetNav() {
    Object.assign(nav, DEFAULT_NAV, { pan: { x: 0, y: 0 }, chase: true });
  }
  function updateCamera(dt) {
    if (nav.chase && !drag.active) {          // ease back to the default chase view
      const k = 1 - Math.exp(-dt * 3);
      nav.yaw += (DEFAULT_NAV.yaw - nav.yaw) * k;
      nav.pitch += (DEFAULT_NAV.pitch - nav.pitch) * k;
      nav.dist += (DEFAULT_NAV.dist - nav.dist) * k;
      nav.pan.x += (0 - nav.pan.x) * k;
      nav.pan.y += (0 - nav.pan.y) * k;
    }
    const turn = 1.6 * dt, zoom = 1.2 * dt, pan = 2.0 * dt * Math.max(1, nav.dist / 7);
    if (keys.has("ArrowLeft")) { nav.yaw -= turn; nav.chase = false; }
    if (keys.has("ArrowRight")) { nav.yaw += turn; nav.chase = false; }
    if (keys.has("ArrowUp")) { nav.pitch = clamp(nav.pitch + turn * 0.6, -0.1, 1.45); nav.chase = false; }
    if (keys.has("ArrowDown")) { nav.pitch = clamp(nav.pitch - turn * 0.6, -0.1, 1.45); nav.chase = false; }
    if (keys.has("=") || keys.has("+")) { nav.dist = clamp(nav.dist * Math.exp(-zoom), 1.5, 60); nav.chase = false; }
    if (keys.has("-")) { nav.dist = clamp(nav.dist * Math.exp(zoom), 1.5, 60); nav.chase = false; }
    if (keys.has("a")) { nav.pan.x -= pan; nav.chase = false; }
    if (keys.has("d")) { nav.pan.x += pan; nav.chase = false; }
    if (keys.has("w")) { nav.pan.y += pan; nav.chase = false; }
    if (keys.has("s")) { nav.pan.y -= pan; nav.chase = false; }

    const cp = Math.cos(nav.pitch);
    const off = new THREE.Vector3(Math.sin(nav.yaw) * cp, Math.sin(nav.pitch), Math.cos(nav.yaw) * cp).multiplyScalar(nav.dist);
    const look = new THREE.Vector3(TARGET.x + nav.pan.x, TARGET.y + nav.pan.y, TARGET.z);
    camera.position.copy(look).add(off);
    camera.lookAt(look);
  }

  el.addEventListener("pointerdown", (e) => {
    drag.active = true; drag.button = e.button; drag.x = e.clientX; drag.y = e.clientY; drag.shift = e.shiftKey;
    nav.chase = false;
    el.setPointerCapture(e.pointerId);
  });
  el.addEventListener("pointermove", (e) => {
    if (!drag.active) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX; drag.y = e.clientY;
    if (drag.button === 2 || e.shiftKey) {
      nav.pan.x -= dx * 0.005 * nav.dist;
      nav.pan.y += dy * 0.005 * nav.dist;
    } else {
      nav.yaw -= dx * 0.006;
      nav.pitch = clamp(nav.pitch + dy * 0.004, -0.1, 1.45);
    }
  });
  const endDrag = () => { drag.active = false; };
  el.addEventListener("pointerup", endDrag);
  el.addEventListener("pointercancel", endDrag);
  el.addEventListener("wheel", (e) => {
    e.preventDefault();
    nav.dist = clamp(nav.dist * Math.exp(e.deltaY * 0.001), 1.5, 60);
    nav.chase = false;
  }, { passive: false });
  el.addEventListener("dblclick", resetNav);
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  const onKeyDown = (e) => {
    if (/INPUT|SELECT|TEXTAREA|BUTTON/.test(e.target.tagName)) return;
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (k === "r") resetNav();
    else if (k === "c") nav.chase = !nav.chase;
    else keys.add(k);
    if (k.startsWith("Arrow") || k === " ") e.preventDefault();
  };
  const onKeyUp = (e) => {
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    keys.delete(k);
  };
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - lastFrame) / 1000);
    lastFrame = now;
    sceneT += dt;

    if (atStart) {
      dispD = 0;                                    // held at the start position during the countdown
    } else if (snap) {
      const v = snap.virtual_speed_ms || 0;
      dispD += v * dt * (1 + surge(strokePhase(performance.now())));
      if (snap.status === "running") dispD += ((snap.virtual_distance_m || 0) - dispD) * (1 - Math.exp(-dt * 4));
    }
    rebuildRiver(dispD);
    placeScenery(dispD);
    placeDolphins(dispD, sceneT, dt);
    placeExtras(dispD, sceneT);
    animateBoat(performance.now());
    wake.scale.set(1, 1, 0.5 + Math.min(1.5, (snap && snap.speed_ms ? snap.speed_ms : 0) / 4));

    updateCamera(dt);
    renderer.render(scene, camera);

    frames++;
    if (now - fpsT >= 1000) {
      if (onFps) onFps(Math.round((frames * 1000) / (now - fpsT)));
      frames = 0;
      fpsT = now;
    }
  }

  raf = requestAnimationFrame(frame);

  return {
    setSnapshot(s) {
      snap = s;
      snapArrivalMs = performance.now();
      if (s.stroke_rate_spm) strokePeriodS = 60 / s.stroke_rate_spm;
      if (lastStrokes !== null && s.strokes !== lastStrokes) lastStrokeMs = performance.now();
      lastStrokes = s.strokes;
      if (s.status === "idle" || s.status === "none") { dispD = 0; lastStrokes = null; }
    },
    setBots(on) { botsOn = !!on; },
    holdAtStart(on) { atStart = !!on; if (atStart) dispD = 0; },
    resetView() { resetNav(); },
    dispose() {
      cancelAnimationFrame(raf);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      if (ro) ro.disconnect();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
