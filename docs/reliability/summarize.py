"""Summarise the soak CSVs: first, last, min, max per column and the slope over the second half (after warm-up)."""

from __future__ import annotations

import csv
import sys


def stats(path: str, cols: list[str]) -> None:
    rows = list(csv.DictReader(open(path)))
    if not rows:
        print(path, "empty")
        return
    print(f"\n{path}: {len(rows)} samples, {int(rows[-1]['t_s'])} s")
    print(f"{'column':<14}{'first':>10}{'at 2 min':>10}{'last':>10}{'min':>10}{'max':>10}{'slope/h (2nd half)':>22}")
    for c in cols:
        vals = []
        for r in rows:
            try:
                vals.append((int(r["t_s"]), float(r[c])))
            except (KeyError, ValueError):
                vals.append((int(r["t_s"]), float("nan")))
        v = [x[1] for x in vals]
        at2 = next((x[1] for x in vals if x[0] >= 120), v[-1])
        half = vals[len(vals) // 2:]
        slope = ((half[-1][1] - half[0][1]) / max(1, half[-1][0] - half[0][0]) * 3600) if len(half) > 1 else 0.0
        print(f"{c:<14}{v[0]:>10.2f}{at2:>10.2f}{v[-1]:>10.2f}{min(v):>10.2f}{max(v):>10.2f}{slope:>22.2f}")


for path in sys.argv[1:]:
    head = open(path).readline().strip().split(",")
    want = [c for c in ("rss_mb", "traced_mb", "fds", "threads", "tasks", "objects", "listeners", "slots", "ws_clients", "ws_info", "store_subs", "store_events", "ring_log", "calendars", "handled", "dab_msgs", "dab_live", "series_cache", "host_cache", "repairs", "sup_bad", "holiday_lru",
                                 "jsheap_used_mb", "jsheap_total_mb", "nodes", "js_listeners", "documents", "frames") if c in head]
    stats(path, want)
    last = list(csv.DictReader(open(path)))[-1]
    counters = {k: last[k] for k in ("msgs", "bytes", "churn_conns", "play_cycles", "rings", "alarm_crud", "inputs", "polls", "config", "timers") if k in last}
    if counters:
        print("counters:", counters)
