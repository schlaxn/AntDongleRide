// Poses for the cat skeleton (cat.js). A pose is plain data; the cat applies it. Feet are the toe
// positions in the cat's root space (y = 0 is the ground, the cat faces -z, +x is its right);
// footLean is how far the long foot bone leans back from vertical (0 = upright on tiptoe).
// Each state function takes the time in the state (walk takes the stride phase 0-1).
import * as THREE from "/static/vendor/three.module.min.js";
import { smooth01 } from "./rig.js";

const V = (x, y, z) => new THREE.Vector3(x, y, z);
const TAU = Math.PI * 2;
const PAW_Y = 0.017;     // toe pad centre height when it stands on the ground

export const STATES = ["stand", "walk", "sprint", "sit", "groom", "stretch"];

export function newPose() {
  return {
    bodyY: 0.245, bodyZ: 0, pitch: 0, yaw: 0, roll: 0, spine: 0,   // spine: the front half pitches against the rear half
    headPitch: 0, headYaw: 0, headRoll: 0, headY: 0.05, headZ: -0.245,   // the head's position on the body
    tailPitch: [0.45, 0.25, 0.05, -0.15, -0.3, -0.4], tailYaw: [0, 0, 0, 0, 0, 0],
    feet: [V(-0.06, PAW_Y, -0.185), V(0.06, PAW_Y, -0.185), V(-0.065, PAW_Y, 0.105), V(0.065, PAW_Y, 0.105)],
    footLean: [0.2, 0.2, 0.35, 0.35],
  };
}

// blend two poses: k = 0 gives a, k = 1 gives b
export function lerpPose(a, b, k) {
  const L = (x, y) => x + (y - x) * k;
  const p = newPose();
  for (const key of ["bodyY", "bodyZ", "pitch", "yaw", "roll", "spine", "headPitch", "headYaw", "headRoll", "headY", "headZ"]) p[key] = L(a[key], b[key]);
  for (let i = 0; i < 6; i++) {
    p.tailPitch[i] = L(a.tailPitch[i], b.tailPitch[i]);
    p.tailYaw[i] = L(a.tailYaw[i], b.tailYaw[i]);
  }
  for (let i = 0; i < 4; i++) {
    p.feet[i] = a.feet[i].clone().lerp(b.feet[i], k);
    p.footLean[i] = L(a.footLean[i], b.footLean[i]);
  }
  return p;
}

// a tail wave: each segment lags the one before it
function tailWave(p, t, amp, speed, lag) {
  for (let i = 0; i < 6; i++) p.tailYaw[i] = amp * Math.sin(TAU * speed * t - i * lag) * (0.5 + i * 0.25);
}

// standing, tail low and swaying slowly, breathing, looking about
export function stand(t = 0) {
  const p = newPose();
  p.bodyY += 0.0015 * Math.sin(TAU * t / 3);
  p.headYaw = 0.3 * Math.sin(TAU * t / 8);
  p.headPitch = 0.05 * Math.sin(TAU * t / 5);
  tailWave(p, t, 0.12, 0.25, 0.7);
  return p;
}

// Walking: a four-beat gait (hind left, front left, hind right, front right) on the toes.
// A paw on the ground moves back against the body at the body's own speed, so it does not slide;
// a paw in the air starts and lands moving at that same speed, so it touches down without a stamp.
// Every paw target stays within reach of its leg (checked numerically: at most 93% of the leg).
export const WALK = { stride: 0.24, duty: 0.65, period: 0.58, lift: 0.045 };
export const walkSpeed = (rate = 1) => (WALK.stride / (WALK.duty * WALK.period)) * rate;   // m/s

// the swing arc of a paw: up quickly, peak early (u = 0.42), down gently; zero vertical speed at
// lift-off and at touchdown, so the paw is placed instead of stamped
const arc = (u) => Math.sin(Math.PI * Math.pow(Math.min(1, Math.max(0, u)), 0.8)) ** 2;

export function walk(phase, t = 0) {
  const p = newPose();
  const S = WALK.stride, D = WALK.duty, H = WALK.lift;
  // lateral sequence measured from video of a walking cat: HL lands at 0, FL 0.13 later, HR at 0.5, FR 0.13 later
  const offsets = [0.87, 0.37, 0, 0.5];                   // FL, FR, HL, HR (q = phase + offset; stance starts at q = 0)
  const centreZ = [-0.12, -0.12, 0.115, 0.115];       // the paws swing about 0.1 m either side of the shoulder / hip
  const track = [-0.06, 0.06, -0.065, 0.065];              // the legs come straight down (front view)
  const baseLean = [0.2, 0.2, 0.35, 0.35];
  const swingLean = [0.4, 0.4, 0.5, 0.5];                  // how much the foot folds in the swing
  const toeOff = [0.1, 0.1, 0.3, 0.3];                     // the heel rises as the paw pushes off
  const slope = (S * (1 - D)) / D;                         // dz/du of the stance speed over the swing
  for (let i = 0; i < 4; i++) {
    const q = (phase + offsets[i]) % 1;
    const c = centreZ[i];
    if (q < D) {
      const a = q / D;
      p.feet[i] = V(track[i], PAW_Y, c - S / 2 + S * a);                                   // stance: backwards
      p.footLean[i] = Math.max(0.02, baseLean[i] - toeOff[i] * smooth01((a - 0.55) / 0.45));
    } else {
      const u = (q - D) / (1 - D);
      const h00 = 2 * u ** 3 - 3 * u ** 2 + 1, h10 = u ** 3 - 2 * u ** 2 + u, h01 = -2 * u ** 3 + 3 * u ** 2, h11 = u ** 3 - u ** 2;
      const z = h00 * (c + S / 2) + h10 * slope + h01 * (c - S / 2) + h11 * slope;         // Hermite: matching speeds
      p.feet[i] = V(track[i], PAW_Y + H * arc(u), z);
      p.footLean[i] = Math.max(0.02, baseLean[i] - toeOff[i] * (1 - smooth01(u * 2)) + swingLean[i] * arc(u));
    }
  }
  p.bodyY = 0.245 + 0.003 * Math.sin(2 * TAU * phase + 0.5);   // tall on straight legs
  p.roll = 0.012 * Math.sin(TAU * phase);
  p.yaw = 0.02 * Math.sin(TAU * phase + 1);
  p.headYaw = -p.yaw;                                      // the head stays steady while the body sways
  p.headPitch = -0.1;
  p.headY = 0.032;                                         // a walking cat carries its head low, ears about level with the back
  p.headZ = -0.258;
  p.tailPitch = [0.35, 0.2, 0.1, 0, -0.1, -0.2];
  for (let i = 0; i < 6; i++) p.tailYaw[i] = 0.1 * Math.sin(TAU * phase - i * 0.5) * (0.6 + i * 0.25);
  return p;
}

// Sprint: a rotary gallop, after Muybridge's plate of a galloping cat. Four phases per stride:
// the hind paws land under the body and drive back, a long extended flight (front paws reaching
// far ahead, hind paws trailing), the front paws land and sweep back under the chest, and a gathered
// flight with the back arched and the hind paws swinging forward under the belly. Paws on the ground
// move back at the body's speed (no sliding); the stance excursion is the same for all four paws.
export const SPRINT = { period: 0.5, duty: 0.27, reach: 0.28, lift: 0.11 };
export const sprintSpeed = (rate = 1) => (SPRINT.reach / (SPRINT.duty * SPRINT.period)) * rate;   // m/s

export function sprint(phase, t = 0) {
  const p = newPose();
  const D = SPRINT.duty, vT = SPRINT.reach / D;            // vT: ground travel per stride relative to the body
  const land = [0.47, 0.42, 0.0, 0.05];                    // FL, FR, HL, HR: phase at which each paw lands
  const duty = [D, D, 0.21, 0.21];                         // the hind paws leave the ground sooner
  const landZ = [-0.25, -0.25, 0.03, 0.03];
  const track = [-0.055, 0.055, -0.06, 0.06];
  const lift = [SPRINT.lift, SPRINT.lift, 0.06, 0.06];
  const baseLean = [0.25, 0.25, 0.35, 0.35];
  const swingLean = [0.5, 0.5, 0.8, 0.8];
  for (let i = 0; i < 4; i++) {
    const d = duty[i], X = vT * d;                         // stance excursion
    const q = (((phase - land[i]) % 1) + 1) % 1;
    if (q < d) {
      const a = q / d;
      p.feet[i] = V(track[i], PAW_Y, landZ[i] + X * a);
      p.footLean[i] = baseLean[i] + 0.2 * smooth01((a - 0.6) / 0.4);   // the heel rises as the paw pushes off
    } else {
      const u = (q - d) / (1 - d);
      const z0 = landZ[i] + X, z1 = landZ[i], sl = vT * (1 - d);       // matching ground speed at both ends
      const h00 = 2 * u ** 3 - 3 * u ** 2 + 1, h10 = u ** 3 - 2 * u ** 2 + u, h01 = -2 * u ** 3 + 3 * u ** 2, h11 = u ** 3 - u ** 2;
      p.feet[i] = V(track[i], PAW_Y + lift[i] * arc(u), h00 * z0 + h10 * sl * 0.35 + h01 * z1 + h11 * sl);   // a gentle start, a ground-speed landing
      p.footLean[i] = baseLean[i] + 0.2 + (swingLean[i] - baseLean[i] - 0.2) * arc(u);
    }
  }
  // airborne: with no paw on the ground the whole cat rises and falls like a thrown object. The two
  // flights are the extension (last hind paw up -> first front paw down) and the gather (last front
  // paw up -> first hind paw down).
  const win = (a0, a1, h) => (phase > a0 && phase < a1 ? h * 4 * ((phase - a0) / (a1 - a0)) * (1 - (phase - a0) / (a1 - a0)) : 0);
  const rise = win(land[3] + duty[3], land[1], 0.035) + win(land[0] + D, 1 + land[2], 0.04);
  if (rise > 0) for (let i = 0; i < 4; i++) p.feet[i].y += rise;
  const cyc = (c, w = 1) => (0.5 + 0.5 * Math.cos(TAU * (phase - c))) ** w;
  const gathered = cyc(0.88, 2), extended = cyc(0.38, 2);
  p.bodyY = 0.2 + 0.02 * gathered + rise;      // high when gathered, low and long when stretched
  p.pitch = 0.16 * gathered - 0.07 * cyc(0.45) + 0.04;      // tail end down in the gather, nose down as the front paws land
  p.spine = -0.38 * gathered + 0.12 * extended;             // the back arches in the gather and stretches in the extension
  p.headPitch = -0.8 * (p.pitch + p.spine) - 0.08;          // the head stays level and looks ahead
  p.headY = 0.03; p.headZ = -0.262;
  p.roll = 0.01 * Math.sin(TAU * phase);
  p.tailPitch = [0.12, 0.05, 0.02, 0, -0.03, -0.05];       // the tail streams out behind
  for (let i = 0; i < 6; i++) p.tailYaw[i] = 0.12 * Math.sin(TAU * phase - i * 0.5) * (0.5 + i * 0.2);
  return p;
}

// sitting upright on the haunches, front legs straight, tail curled round the paws
export function sit(t = 0) {
  const p = newPose();
  p.pitch = 0.7;
  p.bodyY = 0.165;
  p.headPitch = -0.65 + 0.04 * Math.sin(TAU * t / 6);
  p.headYaw = 0.35 * Math.sin(TAU * t / 9);
  p.feet = [V(-0.05, PAW_Y, -0.1), V(0.05, PAW_Y, -0.1), V(-0.075, PAW_Y, -0.05), V(0.075, PAW_Y, -0.05)];
  p.footLean = [0.2, 0.2, 1.45, 1.45];                     // the hind feet lie flat on the ground when sitting
  p.tailPitch = [-0.68, -0.05, 0, 0, 0, 0];
  p.tailYaw = [0.45, 0.45, 0.5, 0.5, 0.45, 0.35];
  const flick = Math.max(0, Math.sin(TAU * t / 4.5)) ** 8;     // now and then the tail tip flicks
  p.tailYaw[4] += 0.5 * flick;
  p.tailYaw[5] += 0.9 * flick;
  p.bodyY += 0.0015 * Math.sin(TAU * t / 3);
  return p;
}

// sitting and washing: the head dips, a front paw comes up to the mouth and moves
export function groom(t = 0) {
  const p = sit(t);
  const bob = Math.sin(TAU * t * 1.8);
  p.headPitch = -1.15 + 0.12 * bob;
  p.headYaw = 0.25;
  p.feet[0] = V(-0.03, 0.2 + 0.03 * bob, -0.19);
  p.footLean[0] = 0.9;
  return p;
}

// a stretch: chest low and front paws forward, rump up, tail high
export function stretch(t = 0) {
  const p = newPose();
  const k = smooth01(Math.min(1, t / 0.8)) * (1 - smooth01((t - 4) / 0.8));   // in, hold, out
  p.pitch = -0.3 * k;
  p.bodyY = 0.23 - 0.04 * k;
  p.feet = [
    V(-0.06, PAW_Y, -0.185 - 0.15 * k), V(0.06, PAW_Y, -0.185 - 0.15 * k),
    V(-0.065, PAW_Y, 0.105 + 0.02 * k), V(0.065, PAW_Y, 0.105 + 0.02 * k),
  ];
  p.headPitch = -0.35 * k;
  p.tailPitch = [-1.3 * k, -0.4 * k, -0.2 * k, -0.1 * k, 0, 0];
  p.tailYaw = [0, 0, 0, 0, 0, 0];
  return p;
}
