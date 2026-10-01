from __future__ import annotations

from dawn_core.display.curve import BrightnessController, Hysteresis, interpolate

CURVE = [(0, 1), (3, 6), (20, 20), (100, 45), (400, 80), (1000, 100)]


def test_interpolate_endpoints_and_midpoints() -> None:
    assert interpolate(CURVE, -5) == 1
    assert interpolate(CURVE, 0) == 1
    assert interpolate(CURVE, 5000) == 100
    assert interpolate(CURVE, 60) == 32.5  # halfway between (20,20) and (100,45)
    assert interpolate(CURVE, 700) == 90
    assert interpolate([(10, 30)], 1) == 30


def test_hysteresis_boolean() -> None:
    h = Hysteresis(low=5, high=7)
    assert h.update(10) is False
    assert h.update(6) is False  # not yet below low
    assert h.update(4) is True
    assert h.update(6) is True  # still night: between thresholds
    assert h.update(8) is False


def test_controller_hysteresis_ignores_small_changes() -> None:
    c = BrightnessController(CURVE, hysteresis_percent=4, slew_s=2, now=0.0)
    c.target_from_lux(100, now=0.0)
    assert c.target == 45
    c.target_from_lux(104, now=0.0)  # 46.25 -> less than 4% away
    assert c.target == 45
    c.target_from_lux(130, now=0.0)  # 48.5 -> still < 4
    assert c.target == 45
    c.target_from_lux(200, now=0.0)  # 56.7
    assert round(c.target, 1) == 56.7


def test_controller_slews_over_two_seconds() -> None:
    c = BrightnessController(CURVE, hysteresis_percent=0, slew_s=2, now=0.0)
    c.jump(20)
    c.set_target(80, now=0.0)
    assert c.step(now=0.0) == 20
    assert c.step(now=1.0) == 50
    assert c.step(now=2.0) == 80
    assert c.step(now=5.0) == 80
    # retarget mid-glide starts from the current applied value
    c.set_target(0, now=5.0)
    assert c.step(now=6.0) == 40.5  # clamped target 1 -> (80 + (1-80)*0.5)


def test_controller_clamps_to_range() -> None:
    c = BrightnessController([(0, 0), (10, 150)], hysteresis_percent=0, slew_s=0, lo=1, hi=100, now=0.0)
    c.target_from_lux(0, now=0.0)
    assert c.step(now=0.0) == 1
    c.target_from_lux(10, now=0.0)
    assert c.step(now=0.0) == 100


def test_night_threshold_scenario_within_three_seconds() -> None:
    """Covering the sensor: night palette and dim within 3 s; uncovering returns within 3 s."""
    c = BrightnessController(CURVE, hysteresis_percent=4, slew_s=2, now=0.0)
    night = Hysteresis(low=5, high=7)
    c.target_from_lux(150, now=0.0)
    c.jump(c.target)
    t = 0.0
    for i in range(30):  # 3 s at 10 Hz, sensor covered (0.5 lx)
        t = i / 10
        c.target_from_lux(0.5, now=t)
        assert night.update(0.5) is True
        c.step(now=t)
    assert c.applied <= 2
    for i in range(30, 60):
        t = i / 10
        c.target_from_lux(150, now=t)
        assert night.update(150) is False
        c.step(now=t)
    assert abs(c.applied - interpolate(CURVE, 150)) < 0.01
