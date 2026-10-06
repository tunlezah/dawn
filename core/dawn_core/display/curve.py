"""Pure brightness maths: piecewise-linear lux curve, hysteresis, slew."""

from __future__ import annotations

import time
from collections.abc import Sequence

Point = tuple[float, float]  # (lux, brightness%)


def interpolate(curve: Sequence[Point], lux: float) -> float:
    """Piecewise-linear interpolation, clamped to the end points."""
    pts = sorted(curve)
    if not pts:
        return 50.0
    if lux <= pts[0][0]:
        return float(pts[0][1])
    if lux >= pts[-1][0]:
        return float(pts[-1][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if x0 <= lux <= x1:
            if x1 == x0:
                return float(y1)
            return y0 + (y1 - y0) * (lux - x0) / (x1 - x0)
    return float(pts[-1][1])


class Hysteresis:
    """Boolean with separate enter/exit thresholds (enter below `low`, exit above `high`)."""

    def __init__(self, low: float, high: float, initial: bool = False):
        self.low, self.high = low, high
        self.state = initial

    def update(self, value: float) -> bool:
        if self.state and value > self.high:
            self.state = False
        elif not self.state and value < self.low:
            self.state = True
        return self.state


class BrightnessController:
    """Target from lux with hysteresis; applied value glides to the target over slew_s."""

    def __init__(self, curve: Sequence[Point], hysteresis_percent: float = 4, slew_s: float = 2.0, lo: float = 1, hi: float = 100, now: float | None = None):
        self.curve = list(curve)
        self.hysteresis = hysteresis_percent
        self.slew_s = slew_s
        self.lo, self.hi = lo, hi
        t = time.monotonic() if now is None else now
        self.target = interpolate(self.curve, 100.0)
        self.applied = self.target
        self._start = self.target
        self._start_t = t

    def _clamp(self, v: float) -> float:
        return max(self.lo, min(self.hi, v))

    def target_from_lux(self, lux: float, now: float | None = None) -> float:
        """Update the target from a lux reading, ignoring changes smaller than the hysteresis."""
        want = self._clamp(interpolate(self.curve, lux))
        if abs(want - self.target) >= self.hysteresis or want in (self.lo, self.hi) and want != self.target:
            self.set_target(want, now)
        return self.target

    def retarget(self, lux: float, now: float | None = None) -> float:
        """Target straight from the curve, without the hysteresis: after a forced level (sleep, a tap, light-wake)
        the old target is not the curve's, and "close enough" to 0 would keep the screen dark."""
        self.set_target(interpolate(self.curve, lux), now)
        return self.target

    def set_target(self, value: float, now: float | None = None) -> None:
        value = self._clamp(value)
        if value == self.target:
            return
        t = time.monotonic() if now is None else now
        self._start = self.applied
        self._start_t = t
        self.target = value

    def step(self, now: float | None = None) -> float:
        """Advance the glide; returns the value to apply."""
        t = time.monotonic() if now is None else now
        if self.slew_s <= 0 or self.applied == self.target:
            self.applied = self.target
            return self.applied
        frac = min(1.0, (t - self._start_t) / self.slew_s)
        self.applied = self._start + (self.target - self._start) * frac
        return self.applied

    def jump(self, value: float) -> None:
        """Apply immediately (used when the slew would be wrong, e.g. light-wake)."""
        value = self._clamp(value)
        self.target = self.applied = self._start = value
