"""Interval workout model: segments, phase lookup and the time-based gradient profile.

Pure logic, no I/O, so it is easy to unit-test.

Shorthand "B-R-ON-OFF" means: B blocks, R reps per block, ON seconds work,
OFF seconds recovery per rep. Warm-up, cool-down and the pause between blocks
are configured separately.
"""
from dataclasses import dataclass, field

from gear import GEAR_RATIOS, START_GEAR, cadence_for, choose_gear
from physics import RoadPhysics

WHEEL_M = 2.105  # default wheel circumference (m), same as the server default
from power import TrainerPowerModel

# Ghost rider per interval type: give a speed or a power, the other is calculated from the power curve.
# Defaults from the last attempt: low = 18 km/h (recovery), high = 27 km/h (work).
DEFAULT_GHOST = {
    "warmup": {"speed_kmh": 18.0},
    "work": {"speed_kmh": 27.0},
    "recover": {"speed_kmh": 18.0},
    "between": {"speed_kmh": 18.0},
    "cooldown": {"speed_kmh": 18.0},
}
GHOST_KINDS = tuple(DEFAULT_GHOST)

DEFAULT_GRADIENTS = {
    "warmup": 1.0,      # percent
    "work": 8.0,
    "recover": -6.0,
    "between": -2.0,
    "cooldown": 0.0,
}
RAMP_S = 3  # centred box filter width (seconds) applied to the gradient profile: gradient transitions take ~3 s


@dataclass(frozen=True)
class Segment:
    kind: str  # warmup | work | recover | between | cooldown
    start: int
    duration: int
    block: int = 0  # 1-based, 0 when not part of a block
    rep: int = 0    # 1-based, 0 when not part of a rep

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass
class Workout:
    warmup_s: int = 600
    cooldown_s: int = 600
    blocks: int = 3
    reps: int = 13
    on_s: int = 30
    off_s: int = 15
    between_s: int = 300
    skip_last_recover: bool = True   # default: the last recovery before the cool-down is skipped
    gradients: dict = field(default_factory=lambda: dict(DEFAULT_GRADIENTS))
    ghost: dict = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_GHOST.items()})

    def ghost_resolved(self) -> dict:
        """Per interval type: the ghost speed and power. A given speed wins; otherwise the speed
        is worked out from the given power. Both are always returned."""
        model = TrainerPowerModel()
        out = {}
        for kind in GHOST_KINDS:
            spec = self.ghost.get(kind) or DEFAULT_GHOST[kind]
            if spec.get("speed_kmh") is not None:
                v = float(spec["speed_kmh"])
                p = model.resistance_w(v)
            elif spec.get("power_w") is not None:
                p = float(spec["power_w"])
                v = model.speed_for_power(p)
            else:
                v = DEFAULT_GHOST[kind]["speed_kmh"]
                p = model.resistance_w(v)
            out[kind] = {"speed_kmh": round(v, 2), "power_w": round(p, 1)}
        return out

    def ghost_speed_per_second(self) -> list[float]:
        """Ghost speed (km/h) for every second, smoothed over 3 s so changes are not abrupt."""
        res = self.ghost_resolved()
        raw = [0.0] * self.total_s
        for s in self.segments():
            for i in range(s.start, s.end):
                raw[i] = res[s.kind]["speed_kmh"]
        out = []
        for i in range(len(raw)):
            lo, hi = max(0, i - 1), min(len(raw), i + 2)
            out.append(round(sum(raw[lo:hi]) / (hi - lo), 3))
        return out

    def ghost_distance_m(self) -> list[float]:
        """Cumulative ghost distance (m) at each second; length total_s + 1."""
        dist = [0.0]
        for v in self.ghost_speed_per_second():
            dist.append(round(dist[-1] + v / 3.6, 3))
        return dist

    def ghost_virtual_profile(self, power_scale: float = 1.0) -> tuple[list[float], list[float], list[float]]:
        """Ghost virtual speed (km/h) and distance (m) per second, from its power on the virtual road
        (gradient, rolling resistance, air drag). Both lists have length total_s + 1."""
        phys = RoadPhysics()
        res = self.ghost_resolved()
        power = [0.0] * self.total_s
        for s in self.segments():
            for i in range(s.start, s.end):
                power[i] = res[s.kind]["power_w"] * power_scale
        grades = self.gradient_per_second()
        v, d, gear = 0.0, 0.0, START_GEAR
        speeds, dists, cadences = [0.0], [0.0], [0.0]
        for i in range(self.total_s):
            v = phys.step(v, power[i], grades[i], 1.0)
            d += v
            gear = choose_gear(v, gear, WHEEL_M)          # virtual gearing for the ghost too
            speeds.append(round(v * 3.6, 3))
            dists.append(round(d, 3))
            cadences.append(round(cadence_for(v, GEAR_RATIOS[gear], WHEEL_M), 2))
        return speeds, dists, cadences

    def __post_init__(self):
        for name in ("blocks", "reps", "on_s", "off_s"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        for name in ("warmup_s", "cooldown_s", "between_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")

    def segments(self) -> list[Segment]:
        segs: list[Segment] = []
        t = 0

        def add(kind, duration, block=0, rep=0):
            nonlocal t
            if duration > 0:
                segs.append(Segment(kind, t, duration, block, rep))
                t += duration

        add("warmup", self.warmup_s)
        for b in range(1, self.blocks + 1):
            for r in range(1, self.reps + 1):
                add("work", self.on_s, b, r)
                last_rep = r == self.reps
                if not (last_rep and self.skip_last_recover and b == self.blocks):
                    add("recover", self.off_s, b, r)
            if b < self.blocks:
                add("between", self.between_s, b)
        add("cooldown", self.cooldown_s)
        return segs

    @property
    def total_s(self) -> int:
        return sum(s.duration for s in self.segments())

    def phase_at(self, t: float) -> Segment | None:
        """Segment active at elapsed time t (seconds). None before start or after the end."""
        if t < 0:
            return None
        for s in self.segments():
            if s.start <= t < s.end:
                return s
        return None

    def raw_gradient_per_second(self) -> list[float]:
        g = [0.0] * self.total_s
        for s in self.segments():
            value = self.gradients.get(s.kind, 0.0)
            for i in range(s.start, s.end):
                g[i] = value
        return g

    def gradient_per_second(self) -> list[float]:
        """Gradient (percent) for each second, smoothed so transitions take ~RAMP_S seconds."""
        raw = self.raw_gradient_per_second()
        half = RAMP_S // 2
        out = []
        for i in range(len(raw)):
            lo, hi = max(0, i - half), min(len(raw), i + half + 1)
            out.append(round(sum(raw[lo:hi]) / (hi - lo), 3))
        return out

    def ghost_levels(self) -> dict:
        """Virtual ghost profiles at -10, -5, 0, +5 and +10% of the interval power (keys "-10" .. "10")."""
        out = {}
        for pct in (-10, -5, 0, 5, 10):
            speed, dist, cad = self.ghost_virtual_profile(1 + pct / 100)
            out[str(pct)] = {"speed_kmh": speed, "distance_m": dist, "cadence_rpm": cad}
        return out

    def to_dict(self) -> dict:
        levels = self.ghost_levels()
        ghost_speed = levels["0"]["speed_kmh"]
        ghost_dist = levels["0"]["distance_m"]
        ghost_cad = levels["0"]["cadence_rpm"]
        return {
            "warmup_s": self.warmup_s,
            "cooldown_s": self.cooldown_s,
            "blocks": self.blocks,
            "reps": self.reps,
            "on_s": self.on_s,
            "off_s": self.off_s,
            "between_s": self.between_s,
            "skip_last_recover": self.skip_last_recover,
            "gradients": self.gradients,
            "ghost": self.ghost_resolved(),
            "ghost_speed_kmh": self.ghost_speed_per_second(),
            "ghost_distance_m": self.ghost_distance_m(),
            "ghost_virtual_speed_kmh": ghost_speed,
            "ghost_virtual_distance_m": ghost_dist,
            "ghost_virtual_cadence_rpm": ghost_cad,
            "ghost_levels": levels,
            "shorthand": f"{self.blocks}-{self.reps}-{self.on_s}-{self.off_s}",
            "total_s": self.total_s,
            "segments": [s.__dict__ | {"end": s.end} for s in self.segments()],
            "gradient_per_second": self.gradient_per_second(),
        }


def parse_shorthand(text: str, **overrides) -> Workout:
    """Parse "3-13-30-15" (blocks-reps-on-off). Extra fields may be passed as keyword args."""
    parts = text.strip().split("-")
    if len(parts) != 4:
        raise ValueError(f"Expected four numbers like 3-13-30-15, got {text!r}")
    try:
        blocks, reps, on_s, off_s = (int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"All four parts must be whole numbers: {text!r}") from exc
    return Workout(blocks=blocks, reps=reps, on_s=on_s, off_s=off_s, **overrides)
