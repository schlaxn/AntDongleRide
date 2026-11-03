// A low-poly cat built from primitives with a simple skeleton: body, head, a six-part tail and
// four legs solved with two-bone IK from foot targets. The cat faces -z, y is up, units are metres
// (about 0.45 m long, 0.25 m to the shoulder). Poses come from gaits.js. The tabby markings are
// painted textures (canvas), not geometry.
import * as THREE from "/static/vendor/three.module.min.js";
import { beam, twoBone } from "./rig.js";

export const COATS = {
  // a European house cat: warm brown mackerel tabby with a light chest and muzzle
  tabby: { base: 0x9a7650, belly: 0xd9c8a6, stripe: 0x45321f },
  ginger: { base: 0xd9873a, belly: 0xf3dcb4, stripe: 0xb4631f },
  black: { base: 0x24272c, belly: 0x30343a, stripe: null },
  white: { base: 0xf3f1ec, belly: 0xffffff, stripe: null, patch: 0xc98a4a },
};

const LEG_NAMES = ["FL", "FR", "HL", "HR"];   // front left, front right, hind left, hind right
const hex = (c) => "#" + c.toString(16).padStart(6, "0");

// ---------- textures (drawn once per coat) ----------
function makeTexture(w, h, draw) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = THREE.RepeatWrapping;
  t.anisotropy = 4;
  return t;
}

// a wavy band across the texture, tapering at both ends, drawn twice so it wraps round the seam
function band(g, w, x0, x1, yc, th, k) {
  for (const shift of [0, -w]) {
    g.beginPath();
    const pts = [];
    for (let x = x0; x <= x1; x += 8) {
      const t = (x - x0) / (x1 - x0);
      const taper = Math.pow(Math.sin(Math.PI * t), 0.6);
      pts.push([x + shift, taper, 3 * Math.sin(x * 0.05 + k)]);
    }
    pts.forEach(([x, tp, wv], i) => (i ? g.lineTo(x, yc - (th / 2) * tp + wv) : g.moveTo(x, yc - (th / 2) * tp + wv)));
    for (let i = pts.length - 1; i >= 0; i--) g.lineTo(pts[i][0], yc + (pts[i][1] * th) / 2 + pts[i][2] * 0.7);
    g.closePath();
    g.fill();
  }
}

// the body: u runs round the body (0.75 = back, 0.25 = belly), v along it (rear to front)
function bodyTexture(C) {
  return makeTexture(512, 256, (g, w, h) => {
    g.fillStyle = hex(C.base);
    g.fillRect(0, 0, w, h);
    // a lighter belly and lower flanks
    const grd = g.createLinearGradient(0.08 * w, 0, 0.42 * w, 0);
    const belly = `${(C.belly >> 16) & 255},${(C.belly >> 8) & 255},${C.belly & 255}`;
    grd.addColorStop(0, `rgba(${belly},0)`);
    grd.addColorStop(0.5, `rgba(${belly},0.95)`);
    grd.addColorStop(1, `rgba(${belly},0)`);
    g.fillStyle = grd;
    g.fillRect(0.08 * w, 0, 0.34 * w, h);
    // mackerel stripes across the back and down the flanks
    g.fillStyle = hex(C.stripe);
    const N = 9;
    for (let k = 0; k < N; k++) {
      band(g, w, 0.3 * w, 1.2 * w, ((k + 0.5) * h) / N, (h / N) * (0.42 + 0.1 * Math.sin(k * 2.1)), k);
    }
    // the dark line along the spine
    g.globalAlpha = 0.85;
    g.fillRect(0.75 * w - 6, 0, 12, h);
    g.globalAlpha = 1;
  });
}

// the head: u = 0.75 is straight ahead; the crown is at the top of the texture
function headTexture(C) {
  return makeTexture(256, 256, (g, w, h) => {
    g.fillStyle = hex(C.base);
    g.fillRect(0, 0, w, h);
    g.strokeStyle = hex(C.stripe);
    g.fillStyle = hex(C.stripe);
    g.lineCap = "round";
    g.lineWidth = 6;
    const fx = 0.75 * w;
    for (const dx of [-14, 0, 14]) {                // forehead stripes running back over the crown
      g.beginPath(); g.moveTo(fx + dx, 0); g.lineTo(fx + dx * 1.2, 0.2 * h); g.stroke();
    }
    g.lineWidth = 5;                                // the tabby "M"
    g.beginPath();
    g.moveTo(fx - 26, 0.34 * h); g.lineTo(fx - 16, 0.24 * h); g.lineTo(fx, 0.32 * h);
    g.lineTo(fx + 16, 0.24 * h); g.lineTo(fx + 26, 0.34 * h);
    g.stroke();
    g.lineWidth = 6;                                // lines from the eyes along the cheeks
    for (const s of [-1, 1]) {
      g.beginPath(); g.moveTo(fx + s * 30, 0.5 * h); g.lineTo(fx + s * 58, 0.56 * h); g.stroke();
      g.beginPath(); g.moveTo(fx + s * 30, 0.57 * h); g.lineTo(fx + s * 54, 0.66 * h); g.stroke();
    }
    g.lineWidth = 8;                                // stripes on the sides of the head
    for (const x of [0.15, 0.3, 0.45, 0.6]) {
      g.beginPath(); g.moveTo(x * w, 0.2 * h); g.lineTo(x * w + 4, 0.5 * h); g.stroke();
    }
  });
}

// rings round a limb or the tail; `at` are positions along it (0-1), `thick` the ring thickness
function ringTexture(C, at, thick) {
  return makeTexture(64, 128, (g, w, h) => {
    g.fillStyle = hex(C.base);
    g.fillRect(0, 0, w, h);
    g.fillStyle = hex(C.stripe);
    for (const a of at) g.fillRect(0, a * h - (thick * h) / 2, w, thick * h);
  });
}

const cache = new Map();
function coatTextures(coat, C) {
  if (cache.has(coat)) return cache.get(coat);
  const set = C.stripe
    ? { body: bodyTexture(C), head: headTexture(C), leg: ringTexture(C, [0.3, 0.75], 0.09), tail: ringTexture(C, [0.5], 0.34) }
    : null;
  cache.set(coat, set);
  return set;
}

export function createCat({ coat = "tabby" } = {}) {
  const C = COATS[coat] || COATS.tabby;
  const tex = coatTextures(coat, C);
  const root = new THREE.Group();
  const body = new THREE.Group();
  root.add(body);

  const lam = (color, map) => new THREE.MeshLambertMaterial({ color: map ? 0xffffff : color, map: map || null, flatShading: true });
  const plain = lam(C.base);
  const bodyMat = tex ? lam(0, tex.body) : plain;
  const headMat = tex ? lam(0, tex.head) : plain;
  const legMat = tex ? lam(0, tex.leg) : plain;
  const tailMat = tex ? lam(0, tex.tail) : plain;
  const belly = lam(C.belly), dark = lam(0x14161a), pink = lam(0xe8a0a8), eye = lam(0xb7e05a);

  const ball = (sx, sy, sz, mat, x, y, z, parent = body) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 18), mat);
    m.scale.set(sx, sy, sz);
    m.position.set(x, y, z);
    parent.add(m);
    return m;
  };
  // a body ellipsoid whose texture runs along the cat (the sphere's poles point along z)
  const longBall = (sx, sy, sz, mat, x, y, z, parent = body) => {
    const geo = new THREE.SphereGeometry(1, 24, 18);
    geo.rotateX(Math.PI / 2);
    const m = new THREE.Mesh(geo, mat);
    m.scale.set(sx, sy, sz);
    m.position.set(x, y, z);
    parent.add(m);
    return m;
  };

  // The spine bends at mid-body: the chest, neck, head and front legs are in `fore`, which pitches
  // against the rest of the body (arching the back in a gallop).
  const fore = new THREE.Group();
  body.add(fore);

  // torso: a long middle, a deeper chest, a rounder haunch, and a lighter belly
  longBall(0.07, 0.078, 0.17, bodyMat, 0, 0, 0);
  longBall(0.075, 0.085, 0.095, bodyMat, 0, 0.003, -0.115, fore);
  longBall(0.08, 0.085, 0.1, bodyMat, 0, 0, 0.115);
  longBall(0.05, 0.055, 0.06, bodyMat, 0, 0.025, -0.175, fore);       // neck
  ball(0.055, 0.045, 0.16, belly, 0, -0.04, 0);
  ball(0.045, 0.05, 0.05, belly, 0, -0.02, -0.17, fore);              // light chest
  if (C.patch) {
    ball(0.045, 0.03, 0.07, lam(C.patch), 0.03, 0.055, 0.02);
    ball(0.04, 0.04, 0.04, lam(C.patch), -0.07, 0.01, 0.12);
  }

  // head: skull, muzzle, nose, ears, eyes
  const head = new THREE.Group();
  head.position.set(0, 0.05, -0.245);
  fore.add(head);
  ball(0.068, 0.062, 0.062, headMat, 0, 0, 0, head);
  ball(0.035, 0.026, 0.032, belly, 0, -0.022, -0.054, head);
  ball(0.012, 0.009, 0.009, pink, 0, -0.008, -0.086, head);
  for (const s of [-1, 1]) {
    const ear = new THREE.Mesh(new THREE.ConeGeometry(0.032, 0.07, 4), plain);
    ear.position.set(s * 0.042, 0.066, 0.004);
    ear.rotation.set(-0.1, 0, -s * 0.28);
    head.add(ear);
    const inner = new THREE.Mesh(new THREE.ConeGeometry(0.018, 0.048, 4), pink);
    inner.position.set(s * 0.042, 0.06, -0.01);
    inner.rotation.copy(ear.rotation);
    head.add(inner);
    ball(0.015, 0.017, 0.009, eye, s * 0.034, 0.014, -0.053, head);
    ball(0.005, 0.014, 0.006, dark, s * 0.034, 0.014, -0.06, head);
  }

  // tail: six segments, each a child of the one before, pitched and yawed by the pose
  const tailRoot = new THREE.Group();
  tailRoot.position.set(0, 0.03, 0.2);
  body.add(tailRoot);
  const tail = [];
  let parent = tailRoot;
  for (let i = 0; i < 6; i++) {
    const seg = new THREE.Group();
    seg.position.z = i === 0 ? 0 : 0.06;
    const m = new THREE.Mesh(new THREE.CylinderGeometry(0.016 - 0.001 * i, 0.021 - 0.001 * i, 0.065, 8), tailMat);
    m.rotation.x = Math.PI / 2;
    m.position.z = 0.03;
    seg.add(m);
    parent.add(seg);
    tail.push(seg);
    parent = seg;
  }

  // legs: a cat walks on its toes. Each leg has three bones: the upper leg, the lower leg, and the
  // long foot bone from the ankle (hind: hock, front: wrist) down to the toes. The pose gives the
  // toe position and how far the foot bone leans back from vertical (footLean).
  const legs = LEG_NAMES.map((name) => {
    const front = name[0] === "F";
    const side = name[1] === "L" ? -1 : 1;
    const upper = new THREE.Mesh(new THREE.CylinderGeometry(front ? 0.03 : 0.052, front ? 0.025 : 0.034, 1, 8), legMat);   // the hind thigh is the haunch
    const lower = new THREE.Mesh(new THREE.CylinderGeometry(front ? 0.024 : 0.03, front ? 0.018 : 0.02, 1, 8), legMat);
    const meta = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.015, 1, 8), legMat);
    const paw = new THREE.Mesh(new THREE.SphereGeometry(1, 10, 8), belly);
    paw.scale.set(0.028, 0.017, 0.038);
    (front ? fore : body).add(upper, lower, meta, paw);
    return {
      name, upper, lower, meta, paw, front,
      L1: front ? 0.10 : 0.07, L2: front ? 0.10 : 0.15, Lm: front ? 0.06 : 0.065,   // hind: a short hidden thigh, a long shin
      // the legs come out of the flank (not the belly): the joints sit at the body's side
      hip: new THREE.Vector3(side * (front ? 0.045 : 0.065), front ? 0.02 : 0.005, front ? -0.115 : 0.115),   // front: the shoulder sits inside the chest, so only the straight forearm shows
      rest: front ? -0.12 : 0.115, follow: front ? 0.5 : 0, maxShift: front ? 0.06 : 0.04,   // the hip is a fixed pivot; the shoulder glides a little, hidden inside the chest
      hint: new THREE.Vector3(0, 0, front ? 1 : -1),   // front: the elbow points back (like a cat's), not forward like a knee; hind: knee forward (hidden)
    };
  });

  const inv = new THREE.Matrix4();
  const invFore = new THREE.Matrix4();
  const local = new THREE.Vector3();
  const ankle = new THREE.Vector3();
  const toe = new THREE.Vector3();
  const hip = new THREE.Vector3();

  // Apply a pose (see gaits.js newPose) to the skeleton.
  function apply(p) {
    body.position.set(0, p.bodyY, p.bodyZ);
    body.rotation.set(p.pitch, p.yaw, p.roll);
    head.position.set(0, p.headY, p.headZ);
    head.rotation.set(p.headPitch, p.headYaw, p.headRoll);
    for (let i = 0; i < tail.length; i++) tail[i].rotation.set(p.tailPitch[i], p.tailYaw[i], 0);
    fore.rotation.set(p.spine || 0, 0, 0);
    body.updateMatrix();
    fore.updateMatrix();
    inv.copy(body.matrix).invert();
    invFore.copy(body.matrix).multiply(fore.matrix).invert();
    for (let i = 0; i < legs.length; i++) {
      const leg = legs[i];
      const foot = p.feet[i];                                 // toe position, in the cat's root space
      const lean = p.footLean[i];                             // foot bone: up and back from the toes
      ankle.set(foot.x, foot.y + leg.Lm * Math.cos(lean), foot.z + leg.Lm * Math.sin(lean));
      const m = leg.front ? invFore : inv;                    // the front legs live on the bending front half
      local.copy(ankle).applyMatrix4(m);                      // root space -> body (or front-half) space
      toe.copy(foot).applyMatrix4(m);
      // the shoulder blade (front) and the pelvis (hind) swing with the stride, so the leg stays near vertical
      const shift = leg.maxShift * Math.tanh((leg.follow * (foot.z - leg.rest)) / leg.maxShift);   // soft limit: no kink
      hip.copy(leg.hip); hip.z += shift;
      const knee = twoBone(hip, local, leg.L1, leg.L2, leg.hint);
      beam(leg.upper, hip, knee);
      beam(leg.lower, knee, local);
      beam(leg.meta, local, toe);
      leg.paw.position.copy(toe);
      leg.paw.rotation.x = -0.9 * Math.max(0, lean - (i < 2 ? 0.1 : 0.45));   // toes tip down when the foot is flexed
    }
  }

  return { group: root, body, fore, head, tail, legs, apply };
}
