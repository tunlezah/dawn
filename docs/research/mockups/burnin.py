"""Burn-in study: where does the standby face hold the same high-contrast pattern all day?

Input: frames from capture-day.mjs (one per 20 min over 24 h, per variant) and the sleep-mode night
frames from render.mjs (SLEEP_FRAMES). For every pixel it sums the hours per day spent on a high-contrast
edge (a luminance step of 8 % or more): the pattern an LCD "remembers" as image retention, and where an
OLED would wear unevenly. It also sums lit-hours (time x luminance, the OLED wear proxy) and the light
the face gives off at night relative to today's night palette.

  pip install numpy pillow
  python burnin.py <frames-dir>          # writes ../img/burnin-heatmap.png, prints the numbers
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

STEP_H = 20 / 60
EDGE = 0.08
NIGHT = lambda name: int(name[:2]) < 6 or int(name[:2]) >= 22


def load(path):
    a = np.asarray(Image.open(path).convert('RGB'), dtype=np.float32) / 255.0
    return a, 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def light(path):
    """Mean linear-light luminance: what the panel emits at a given backlight level (sRGB decoded)."""
    a = np.asarray(Image.open(path).convert('RGB'), dtype=np.float32) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    return float((0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]).mean())


def edges(y):
    p = np.pad(y, 1, mode='edge')
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return np.hypot(gx, gy) / 4.0 > EDGE


def study(files):
    s = w = None
    night_light = 0.0
    for f in files:
        _, y = load(f)
        e = edges(y)
        s = e * STEP_H if s is None else s + e * STEP_H
        w = y * STEP_H if w is None else w + y * STEP_H
        if NIGHT(os.path.basename(f)):
            night_light += light(f)
    return s, w, night_light


def stats(s, w, night_light, ref_night):
    n = s.size
    return {
        'pixels_edge_ge_8h_pct': round(100 * float((s >= 8).sum()) / n, 2),
        'pixels_edge_ge_16h_pct': round(100 * float((s >= 16).sum()) / n, 2),
        'pixels_edge_ge_23h_pct': round(100 * float((s >= 23).sum()) / n, 3),
        'max_edge_hours': round(float(s.max()), 1),
        'p99_lit_hours': round(float(np.percentile(w, 99)), 1),
        'max_lit_hours': round(float(w.max()), 1),
        'night_light_vs_today_pct': round(100 * night_light / ref_night, 1) if ref_night else None,
    }


def heat(s, bg):
    """Overlay hours/day (0..24) on a darkened greyscale frame: violet -> orange -> pale yellow."""
    t = np.clip(s / 24.0, 0, 1)[..., None]
    stops = np.array([[0.35, 0.10, 0.55], [0.95, 0.45, 0.12], [1.0, 0.93, 0.62]], dtype=np.float32)
    col = np.where(t < 0.5, stops[0] + (stops[1] - stops[0]) * (t / 0.5), stops[1] + (stops[2] - stops[1]) * ((t - 0.5) / 0.5))
    alpha = np.clip(t * 1.6, 0, 0.95)
    g = bg.mean(axis=2, keepdims=True) * 0.32
    out = g * (1 - alpha) + col * alpha
    return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))


def font(size, bold=False):
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main(frames):
    names = sorted(os.listdir(os.path.join(frames, 'today')))
    day = [n for n in names if not NIGHT(n)]
    night = [n for n in names if NIGHT(n)]
    variants = {
        'today': [os.path.join(frames, 'today', n) for n in names],
        'orbit': [os.path.join(frames, 'orbit', n) for n in names],
        'orbit + strip auto-hide': [os.path.join(frames, 'orbit-autohide', n) for n in names],
        'recommended': [os.path.join(frames, 'orbit-autohide', n) for n in day] + [os.path.join(frames, 'sleep-night', n) for n in night],
    }
    results, maps = {}, {}
    ref_night = None
    for k, files in variants.items():
        s, w, nl = study(files)
        if k == 'today':
            ref_night = nl
        results[k] = stats(s, w, nl, ref_night)
        maps[k] = s
        print(k, json.dumps(results[k]))

    W, H, pad, top, legend = 800, 480, 24, 76, 70
    fig = Image.new('RGB', (2 * W + 3 * pad, H + top + legend + pad), (7, 9, 13))
    d = ImageDraw.Draw(fig)
    panels = (('today', 'today', 'Today: standby face as shipped'),
              ('recommended', 'orbit-autohide', 'Recommended: pixel orbit + strip auto-hide by day, sleep clock at night'))
    for i, (k, bgv, title) in enumerate(panels):
        x = pad + i * (W + pad)
        bg, _ = load(os.path.join(frames, bgv, '1400.png'))
        fig.paste(heat(maps[k], bg), (x, top))
        r = results[k]
        d.text((x, 14), title, fill=(232, 236, 243), font=font(18, True))
        d.text((x, 42), f"{r['pixels_edge_ge_8h_pct']:.1f}% of pixels on a fixed edge >= 8 h/day  ·  {r['pixels_edge_ge_23h_pct']:.1f}% around the clock  ·  max {r['max_edge_hours']:.0f} h",
               fill=(150, 158, 175), font=font(15))
    # legend
    lx, ly, lw = pad, top + H + 22, 420
    ramp = np.linspace(0, 24, lw)[None, :].repeat(14, 0)
    fig.paste(heat(ramp, np.ones((14, lw, 3), dtype=np.float32) * 0.0), (lx, ly))
    for h in (0, 8, 16, 24):
        d.text((lx + int(lw * h / 24), ly + 20), f'{h} h', fill=(150, 158, 175), font=font(13), anchor='ma')
    d.text((lx + lw + 24, ly - 2), 'Hours per day a pixel sits on the same high-contrast edge (luminance step >= 8 %),', fill=(190, 196, 208), font=font(14))
    d.text((lx + lw + 24, ly + 18), 'from 72 renders of the real face across one simulated day (lit room 06-22, dark 22-06).', fill=(150, 158, 175), font=font(14))
    here = os.path.dirname(os.path.abspath(__file__))
    fig.save(os.path.join(here, '..', 'img', 'burnin-heatmap.png'), optimize=True)
    with open(os.path.join(here, 'data', 'burnin-stats.json'), 'w', encoding='utf-8') as fh:
        json.dump(results, fh, indent=1)


if __name__ == '__main__':
    main(sys.argv[1])
