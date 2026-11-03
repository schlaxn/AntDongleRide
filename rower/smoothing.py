"""Adaptive smoothing for a per-second series (speed or power).

While the value is steady, it is averaged with an exponential window of TAU_S seconds.
When a new level is sustained (the value stays outside the band around the average for
CONFIRM_S seconds), the average jumps to that new level at once, so a deliberate change
shows as soon as it is confirmed, without the 15 s lag.
"""
from dataclasses import dataclass, field


@dataclass
class AdaptiveSmoother:
    tau_s: float = 15.0        # steady averaging window
    band: float = 0.05         # relative band around the average: outside it counts as a change
    confirm_s: float = 2.0     # a change must persist this long before the average follows it
    dt_s: float = 1.0          # spacing of the samples (the raw series is per second)
    avg: float | None = None
    _pending: list[float] = field(default_factory=list)
    _pending_s: float = 0.0

    def update(self, y: float, dt_s: float | None = None) -> float:
        dt = self.dt_s if dt_s is None or dt_s <= 0 else dt_s   # the time since the previous value
        if self.avg is None or self.avg <= 0:
            self.avg = y
            return self.avg
        dev = abs(y - self.avg) / self.avg
        if dev > self.band:
            self._pending.append(y)
            self._pending_s += dt
            if self._pending_s >= self.confirm_s:
                # the change is confirmed: follow it with the recent values of the change
                self.avg = sum(self._pending) / len(self._pending)
                self._pending, self._pending_s = [], 0.0
            return self.avg
        # steady: the change was a blip; drop it and average normally
        self._pending, self._pending_s = [], 0.0
        alpha = min(1.0, dt / self.tau_s)
        self.avg += (y - self.avg) * alpha
        return self.avg


def smooth_series(values: list[float], **kw) -> list[float]:
    sm = AdaptiveSmoother(**kw)
    return [sm.update(v) for v in values]
