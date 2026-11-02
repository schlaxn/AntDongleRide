"""Boat physics for the virtual scull. No gradient: a flat, moving river.

Drag is c·v² (the force), so the steady state of P = F·v is P = c·v³. That is the same
law the monitor uses (W = 1.78·v³ at 17.0 L), so the virtual boat settles at the speed
the rower's power would give on the monitor.

    m·dv/dt = P/v - c·v²

P/v uses max(v, 1.0) so the start does not divide by zero.
"""
import math

DEFAULT_MASS_KG = 90.0      # scull, sculler and water in the boat (rough)
DEFAULT_DRAG_C = 1.78       # W per (m/s)^3, the user's cube-law constant
MAX_SUBSTEP_S = 0.05


class BoatPhysics:
    def __init__(self, mass_kg: float = DEFAULT_MASS_KG, drag_c: float = DEFAULT_DRAG_C):
        if mass_kg <= 0 or drag_c <= 0:
            raise ValueError("mass and drag constant must be positive")
        self.mass_kg = mass_kg
        self.drag_c = drag_c

    def params(self) -> dict:
        return {"mass_kg": self.mass_kg, "drag_c": self.drag_c}

    def accel(self, v: float, power_w: float) -> float:
        v_eff = max(v, 1.0)
        return (power_w / v_eff - self.drag_c * v * v) / self.mass_kg

    def step(self, v: float, power_w: float, dt: float) -> float:
        """Advance speed by dt seconds under constant power. Never negative."""
        if dt <= 0:
            return v
        n = max(1, math.ceil(dt / MAX_SUBSTEP_S))
        h = dt / n
        for _ in range(n):
            v = max(0.0, v + self.accel(v, power_w) * h)
        return v

    def steady_speed(self, power_w: float) -> float:
        """Closed form of P = c·v³."""
        if power_w <= 0:
            return 0.0
        return (power_w / self.drag_c) ** (1.0 / 3.0)
