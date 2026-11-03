// Small rigging helpers shared by the animals (and usable by any procedural figure).
import * as THREE from "/static/vendor/three.module.min.js";

const _up = new THREE.Vector3(0, 1, 0);
const _d = new THREE.Vector3();

// Place a unit-height cylinder (along y, centred) so that it runs from point a to point b.
export function beam(mesh, a, b) {
  _d.subVectors(b, a);
  const len = _d.length() || 1e-6;
  mesh.position.addVectors(a, b).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(_up, _d.normalize());
  mesh.scale.set(1, len, 1);
}

// Two-bone solve: the middle joint (knee, elbow) of a limb with bones of length L1 and L2
// from point a to point c, bent towards `hint`. Out-of-reach targets give a straight limb.
export function twoBone(a, c, L1, L2, hint) {
  const u = new THREE.Vector3().subVectors(c, a);
  const d = Math.min(Math.max(u.length(), 1e-4), L1 + L2 - 1e-3);
  u.normalize();
  const a1 = (L1 * L1 - L2 * L2 + d * d) / (2 * d);
  const h = Math.sqrt(Math.max(0, L1 * L1 - a1 * a1));
  const base = a.clone().addScaledVector(u, a1);
  const perp = hint.clone().addScaledVector(u, -hint.dot(u));
  if (perp.lengthSq() < 1e-8) perp.set(0, 1, 0);
  return base.addScaledVector(perp.normalize(), h);
}

export const smooth01 = (x) => {
  const t = Math.min(1, Math.max(0, x));
  return t * t * (3 - 2 * t);
};
