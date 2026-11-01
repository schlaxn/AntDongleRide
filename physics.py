"""Road physics for the virtual speed.

The trainer's measured speed gives the power the trainer absorbs (power.py). That power is
then used on the virtual road, where gradient, rolling resistance and air drag decide the speed:

    P = v * (F_roll + F_grav + F_aero)          quasi steady
    m dv/dt = P / v - F_roll - F_grav - F_aero  integrated here

Assumptions (adjust to your own values):
    mass      76 kg   rider + bike
    Crr       0.005   rolling resistance (road tyres on tarmac)
    CdA       0.32    m^2, road position, approximate
    rho       1.2     kg/m^3, air density
"""
import math

G = 9.81


class RoadPhysics:
    def __init__(self, mass_kg: float = 76.0, crr: float = 0.005, cda: float = 0.32, rho: float = 1.2):
        self.mass = mass_kg
        self.crr = crr
        self.cda = cda
        self.rho = rho

    def params(self) -> dict:
        return {"mass_kg": self.mass, "crr": self.crr, "cda_m2": self.cda, "rho_kg_m3": self.rho}

    def accel(self, v: float, power_w: float, grade_pct: float) -> float:
        """Acceleration (m/s^2) at speed v (m/s) with power P (W) on a gradient (%)."""
        theta = math.atan(grade_pct / 100.0)
        f_roll = self.crr * self.mass * G * math.cos(theta)
        f_grav = self.mass * G * math.sin(theta)
        f_aero = 0.5 * self.rho * self.cda * v * abs(v)
        v_eff = max(v, 1.0)  # avoid the 1/v blow-up when starting from rest
        return (power_w / v_eff - f_roll - f_grav - f_aero) / self.mass

    def step(self, v: float, power_w: float, grade_pct: float, dt: float) -> float:
        """New speed (m/s) after dt seconds. Sub-stepped for stability; never negative."""
        if dt <= 0:
            return v
        n = max(1, int(math.ceil(dt / 0.05)))
        h = dt / n
        for _ in range(n):
            v = max(0.0, v + h * self.accel(v, power_w, grade_pct))
        return v

    def steady_speed(self, power_w: float, grade_pct: float) -> float:
        """Speed (m/s) at which the power balances the forces (bisection), for checks and tests."""
        lo, hi = 0.0, 40.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if self.accel(mid, power_w, grade_pct) > 0:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
