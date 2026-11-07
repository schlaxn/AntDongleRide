"""Virtual power for an Elite Qubo Power Fluid roller (no power meter).

Fluid trainers absorb power roughly with a power law of speed. The curve is fitted to
power measured with Favero Assioma pedals (7 Nov 2026) in the settled state, after about
10-15 minutes of riding (a 50 minute interval ride, quasi-steady seconds only):
    105 W at 20 km/h, exponent 1.95   (R2 0.99 over 14-32 km/h)
The 40 km/h point (406 W) is the same power law extrapolated: nothing was measured above
about 32 km/h. This gives P = 105 * (v / 20) ** n with n = ln(406/105) / ln(2) ~= 1.95.

History: the first calibration (145 W at 20 km/h from a user measurement, 540 W at 40 km/h
from the manufacturer rating, exponent 1.90) overestimated the measured power by 27-39 %;
the manufacturer chart reads about 300 W at 30 km/h where the pedals measured about 231 W.
In the first ~10 minutes the same speed took about 20 % less power (83 W at 20 km/h); this
model uses the settled state. Pedal power is measured at the crank and includes drivetrain
losses of a few percent.

Flywheel inertia adds power while the speed changes: a solid disc of mass m rolling at
surface speed v stores 0.5*m*v^2, so its kinetic power is 0.5*m*v*dv/dt (radius cancels).
Rider and bike inertia are not modelled (no mass input), so acceleration is underestimated.
"""
import math
from dataclasses import dataclass

REF_SPEED_KMH = 20.0
REF_POWER_W = 105.0
TOP_SPEED_KMH = 40.0
TOP_POWER_W = 406.0   # extrapolated with the measured exponent, not measured
FLYWHEEL_KG = 1.7


@dataclass
class TrainerPowerModel:
    ref_speed_kmh: float = REF_SPEED_KMH
    ref_power_w: float = REF_POWER_W
    top_speed_kmh: float = TOP_SPEED_KMH
    top_power_w: float = TOP_POWER_W
    flywheel_kg: float = FLYWHEEL_KG

    @property
    def exponent(self) -> float:
        return math.log(self.top_power_w / self.ref_power_w) / math.log(self.top_speed_kmh / self.ref_speed_kmh)

    def resistance_w(self, speed_kmh: float) -> float:
        """Power absorbed by the trainer at a steady speed."""
        if speed_kmh <= 0:
            return 0.0
        return self.ref_power_w * (speed_kmh / self.ref_speed_kmh) ** self.exponent

    def speed_for_power(self, power_w: float) -> float:
        """Inverse of resistance_w: the steady speed (km/h) at which the trainer absorbs power_w."""
        if power_w <= 0:
            return 0.0
        return self.ref_speed_kmh * (power_w / self.ref_power_w) ** (1.0 / self.exponent)

    def power_w(self, speed_kmh: float, accel_ms2: float = 0.0) -> float:
        """Estimated rider power: trainer resistance plus flywheel kinetic power."""
        v = speed_kmh / 3.6
        inertia = 0.5 * self.flywheel_kg * v * accel_ms2
        return max(0.0, self.resistance_w(speed_kmh) + inertia)
