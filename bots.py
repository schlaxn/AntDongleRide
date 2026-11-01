"""Other riders on the road: encounters that the rider passes, or that pass the rider.

Everything runs on the server, in ride time, so a page reload or a pause cannot lose the riders.
Each rider follows the rider's own interval plan: its power per interval kind is a slow average of
the rider's own power (its "capability"), scaled by its archetype and a personal factor. It rides
on the same road physics as the rider, so climbs and gradients affect it the same way.

Over a ride about 2 encounters per minute are scheduled. About 60 % of them start ahead of the
rider (the rider passes them), about 40 % start behind (they pass the rider).
"""
import math
import random
from dataclasses import dataclass

from physics import RoadPhysics

RATE_PER_MIN = 8.0        # encounters per minute of workout (testing rate; about 430 in a 54 minute ride)
SLOWER_SHARE = 0.6        # share of encounters where the rider passes the other rider
MAX_ACTIVE = 60           # riders on the road at the same time; an encounter that finds no free slot is missed
SPAWN_AHEAD_M = 60.0      # a rider that the rider passes starts this far ahead (in view, catchable)
SPAWN_BEHIND_M = 30.0     # a rider that passes the rider starts this far behind (inside the 40 m view behind)
PASS_HYST_M = 2.0         # the gap must change sign by this much to count as a pass
PASS_TIMEOUT_S = 240.0    # without a pass after this long, the pass is pushed for
PUSH_RATE = 0.003         # per second: pace nudge towards the pass once the timeout has run out
MAX_LIFE_S = 900.0        # no encounter lasts longer than this
DROP_BEHIND_M = 160.0     # a rider that has been passed is removed this far behind the rider
DROP_AHEAD_M = 340.0      # a rider that has passed the rider is removed this far ahead
PACE_BAND_M = 100.0       # beyond this gap a rider eases back towards the rider
CAP_TAU_S = 120.0         # time constant of the rider's capability average
GEAR_RATIO = 2.6          # gear used for the riders' cadence
WHEEL_M = 2.105
LANE_SLOTS = [-2.25, -1.75, -1.25, -0.75, 0.75, 1.25]   # never on the centre marker or the rider's lane (x = 2.5)
DEFAULT_CAP = {"warmup": 119.0, "work": 256.0, "recover": 119.0, "between": 119.0, "cooldown": 119.0}

ARCHETYPES = {
    "Attacker": {"nick": "Attack", "jersey": 0xD62828, "mult": {"work": 1.10, "recover": 0.80}},
    "Diesel":   {"nick": "Diesel", "jersey": 0x6F88A3, "flat": True},          # ignores the intervals
    "Climber":  {"nick": "Climb", "jersey": 0x2EA043, "climb": 0.02},          # +2 % power per % grade
    "Rouleur":  {"nick": "Roule", "jersey": 0xF2C200, "mult": {}},             # follows the plan
    "Fader":    {"nick": "Fade", "jersey": 0xFF69B4, "fade": 0.01},            # loses 1 % per minute
    "Sprinter": {"nick": "Sprint", "jersey": 0xF2F2F2, "surge": True},         # short surges
}
FIRST_NAMES = ["Anna", "Dieter", "Lukas", "Sofia", "Markus", "Elena", "Jonas", "Petra", "Tobias", "Mia",
               "Felix", "Nora", "Stefan", "Lena", "Paul", "Julia", "Simon", "Katrin", "Max", "Hanna"]


@dataclass
class Bot:
    id: int
    name: str
    arch: str
    role: str            # "slower" (the rider passes it) or "faster" (it passes the rider)
    jersey: int
    lane: float
    d: float             # absolute road distance (m)
    v: float             # m/s
    factor: float        # personal share of the rider's capability
    spawned_at: float    # ride time (s)
    surge: tuple | None  # (start, end) ride time of a surge, Sprinter only
    side: int            # +1 ahead of the rider, -1 behind it
    passed: bool = False
    life: float = 0.0
    cad: float = 0.0


class Traffic:
    def __init__(self, total_s: float, seed: int | None = None):
        self.rng = random.Random(seed)
        self.total_s = float(total_s)
        self.physics = RoadPhysics()
        self.cap = dict(DEFAULT_CAP)
        self.t = 0.0
        n = max(1, round(self.total_s / 60.0 * RATE_PER_MIN))
        spacing = self.total_s / n
        self.schedule = sorted((i + 0.5 + self.rng.uniform(-0.4, 0.4)) * spacing for i in range(n))
        self.next_i = 0
        self.bots: list[Bot] = []
        self.next_id = 1
        self.counts = {"met": 0, "passed": 0, "passed_by": 0}
        self.slower_n = 0    # encounters started ahead of the rider (the rider passes them)

    # ----- one step -----
    def update(self, dt: float, elapsed: float, rider_d: float, rider_v: float, rider_power: float,
               kind: str | None, grade: float) -> None:
        """Advance every rider by dt seconds of ride time. Call only while the ride is running."""
        if dt <= 0:
            return
        self.t = elapsed
        if kind in self.cap and rider_power > 0:
            k = 1.0 - math.exp(-dt / CAP_TAU_S)
            self.cap[kind] += (rider_power - self.cap[kind]) * k
        while self.next_i < len(self.schedule) and self.schedule[self.next_i] <= elapsed:
            if len(self.bots) < MAX_ACTIVE:
                self._spawn(rider_d, rider_v)
            self.next_i += 1          # a due encounter with no free slot is missed, not queued
        for b in list(self.bots):
            gap = b.d - rider_d
            pace = 1.0
            if gap > PACE_BAND_M:
                pace = 1.0 - min(0.3, (gap - PACE_BAND_M) / 500.0)
            elif gap < -PACE_BAND_M:
                pace = 1.0 + min(0.3, (-gap - PACE_BAND_M) / 500.0)
            power = self._power(b, kind, grade, elapsed) * pace
            b.v = self.physics.step(b.v, power, grade, dt)
            b.d += b.v * dt
            b.life += dt
            b.cad = b.v * 60.0 / (GEAR_RATIO * WHEEL_M)
            gap = b.d - rider_d
            # pass detection with hysteresis
            new_side = 1 if gap > PASS_HYST_M else -1 if gap < -PASS_HYST_M else b.side
            if new_side != b.side:
                if b.side == 1 and new_side == -1:
                    self.counts["passed"] += 1      # the rider went past it
                    b.passed = True
                elif b.side == -1 and new_side == 1:
                    self.counts["passed_by"] += 1   # it went past the rider
                    b.passed = True
                b.side = new_side
            # the pass is pushed for once the timeout has run out
            if not b.passed and b.life > PASS_TIMEOUT_S:
                push = 1.0 - PUSH_RATE * dt if b.role == "slower" else 1.0 + PUSH_RATE * dt
                b.factor = min(1.4, max(0.6, b.factor * push))
            gone = (b.passed and ((b.side == -1 and gap < -DROP_BEHIND_M) or (b.side == 1 and gap > DROP_AHEAD_M)))
            gone = gone or b.life > MAX_LIFE_S or abs(gap) > DROP_AHEAD_M + 60
            if gone:
                self.bots.remove(b)

    def _spawn(self, rider_d: float, rider_v: float) -> None:
        n = self.counts["met"]
        role = "slower" if self.slower_n < SLOWER_SHARE * (n + 1) else "faster"
        arch = self.rng.choice(list(ARCHETYPES))
        a = ARCHETYPES[arch]
        name = f"{a['nick']} {self.rng.choice(FIRST_NAMES)}"
        factor = self.rng.uniform(0.80, 0.97) if role == "slower" else self.rng.uniform(1.03, 1.25)
        surge = None
        if a.get("surge"):
            start = self.t + self.rng.uniform(120.0, 900.0)
            surge = (start, start + self.rng.uniform(15.0, 20.0))
        if role == "slower":
            d, side = rider_d + SPAWN_AHEAD_M, 1
        else:
            d, side = rider_d - SPAWN_BEHIND_M, -1
        used = [b.lane for b in self.bots if abs(b.d - d) < 3.0]
        free = [x for x in LANE_SLOTS if x not in used] or LANE_SLOTS
        bot = Bot(id=self.next_id, name=name, arch=arch, role=role, jersey=a["jersey"],
                  lane=self.rng.choice(free), d=d, v=rider_v, factor=factor, spawned_at=self.t,
                  surge=surge, side=side)
        self.next_id += 1
        self.counts["met"] += 1
        if role == "slower":
            self.slower_n += 1
        self.bots.append(bot)

    def _power(self, b: Bot, kind: str | None, grade: float, elapsed: float) -> float:
        a = ARCHETYPES[b.arch]
        if a.get("flat"):
            base = sum(self.cap.values()) / len(self.cap)
        else:
            base = self.cap.get(kind or "recover", self.cap["recover"]) * a.get("mult", {}).get(kind, 1.0)
        base *= 1.0 + a.get("climb", 0.0) * max(0.0, grade)
        base *= max(0.7, 1.0 - a.get("fade", 0.0) * (elapsed - b.spawned_at) / 60.0)
        if b.surge and b.surge[0] <= elapsed < b.surge[1]:
            base *= 1.3
        return base * b.factor

    # ----- output -----
    def snapshot(self, rider_d: float) -> dict:
        riders = [{
            "id": b.id, "name": b.name, "arch": b.arch, "jersey": b.jersey, "lane_x": b.lane,
            "distance_m": round(b.d, 2), "speed_kmh": round(b.v * 3.6, 2), "cadence_rpm": round(b.cad, 1),
        } for b in self.bots]
        return {"riders": riders, "counts": dict(self.counts)}
