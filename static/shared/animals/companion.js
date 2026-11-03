// A cat that keeps pace with the rider or the boat: a companion for the ride and row worlds.
//
// The world tells it how fast the rider goes (update(dt, v)) and reads where the cat is (`off`,
// metres along the path, relative to the rider; + = ahead) and places `group` there. The cat runs
// at the rider's speed up to its own top speed. A faster rider leaves it behind; once it is far
// behind it turns up ahead again, waits for the rider and sets off after them once more.
//
// Gait by speed: stands (still), walks (up to about 1 m/s) and sprints (above). The pace of each
// gait is scaled so the paws on the ground move back at exactly the cat's speed (no sliding).
import { createCat } from "./cat.js";
import { SPRINT, WALK, lerpPose, sprint, sprintSpeed, stand, walk, walkSpeed } from "./gaits.js";
import { smooth01 } from "./rig.js";

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export function createCompanion({
  coat = "ginger",
  scale = 1,          // the cat is 0.45 m long; the worlds make it bigger so it can be seen
  vmax = 4.1,         // top speed (m/s)
  target = 2,         // where it likes to be relative to the rider (m ahead)
} = {}) {
  const cat = createCat({ coat });
  const group = cat.group;
  group.scale.setScalar(scale);
  group.traverse((o) => { if (o.isMesh) o.castShadow = true; });

  let off = target, mode = "follow";      // "follow" | "wait" (ahead, standing, until the rider is near)
  let speed = 0, gait = "stand", phase = 0, gaitT = 0;
  let from = null, blendT = 1, lastPose = null;

  function pick(vc) {
    if (vc < 0.12) return "stand";
    if (gait === "sprint") return vc < 0.8 ? "walk" : "sprint";
    return vc > 1.05 ? "sprint" : "walk";
  }

  function update(dt, v) {
    v = Math.max(0, v || 0);
    if (mode === "wait") {
      speed = 0;
      off -= v * dt;                                    // the rider approaches
      if (off < target + 10) mode = "follow";
    } else {
      const want = v + 1.5 * (target - off);            // catch up if behind, ease off if too far ahead
      speed = clamp(want, 0, vmax);
      if (v < 0.05 && Math.abs(target - off) < 0.4) speed = 0;
      off += (speed - v) * dt;
      if (off < target - 45) { off = target + 40; mode = "wait"; speed = 0; }   // left far behind: turn up ahead again
    }

    const g = pick(speed);
    if (g !== gait) { from = lastPose; blendT = 0; gait = g; gaitT = 0; }
    gaitT += dt;
    blendT += dt / 0.35;
    let pose;
    if (gait === "sprint") {
      const rate = clamp(speed / sprintSpeed(1), 0.5, 2.0);
      phase = (phase + (dt * rate) / SPRINT.period) % 1;
      pose = sprint(phase, gaitT);
    } else if (gait === "walk") {
      const rate = clamp(speed / walkSpeed(1), 0.4, 1.8);
      phase = (phase + (dt * rate) / WALK.period) % 1;
      pose = walk(phase, gaitT);
    } else {
      pose = stand(gaitT);
    }
    if (from && blendT < 1) pose = lerpPose(from, pose, smooth01(blendT));
    lastPose = pose;
    cat.apply(pose);
  }

  return { group, update, get off() { return off; }, get speed() { return speed; }, get gait() { return gait; } };
}
