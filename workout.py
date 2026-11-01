"""Interval workout model: segments, phase lookup and the time-based gradient profile.

Pure logic, no I/O, so it is easy to unit-test.

Shorthand "B-R-ON-OFF" means: B blocks, R reps per block, ON seconds work,
OFF seconds recovery per rep. Warm-up, cool-down and the pause between blocks
are configured separately.
"""
from dataclasses import dataclass, field

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
    skip_last_recover: bool = True   # default: the last recovery of each block is skipped (rest or cool-down follows)
    gradients: dict = field(default_factory=lambda: dict(DEFAULT_GRADIENTS))

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
                # the last rep of a block skips its recovery; the longer between-block rest (or the
                # cool-down, after the last block) takes its place
                if not (last_rep and self.skip_last_recover):
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

    def to_dict(self) -> dict:
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
