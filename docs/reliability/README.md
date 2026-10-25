# Reliability checks

The tools behind [`../RELIABILITY-ASSESSMENT.md`](../RELIABILITY-ASSESSMENT.md). Apart from the regression tests
in `core/tests`, none of them run in CI: the soaks take a quarter of an hour each.

| File | What it does | How to run |
|---|---|---|
| `core/tests/test_reliability_round2.py` | The regression tests for findings 1 to 5 of the assessment (a stamp without a zone, a sleep fade stopping the ramp, a hung preparation, `missed_grace_minutes: 0`, the immutable read-only open ignoring the WAL), fixed in 0.2.4. They run with the suite. | `make test` |
| `test_walk.py` | Walks the real engine through three days across the April DST change with seven alarm kinds, and through a matrix of clock steps. Passing means the engine fired exactly what it should. About two minutes. | as above, with `test_walk.py` |
| `ro_writer.py`, `ro_reader.py` | Dawn's own database open path against a card that has gone read-only: write with `Database`, stop cleanly or crash, remount the directory read-only (`unshare -m` and a bind mount), open again and read the alarms. | `python ro_writer.py <dir>/dawn.db crash` then `unshare -m sh -c "mount --bind <dir> <dir> && mount -o remount,bind,ro <dir> && python ro_reader.py <dir>"` |
| `soak_driver.py` | A plain `dawn-core` process driven hard through its API and WebSocket for `SOAK_MINUTES` (15), with RSS, threads and descriptors sampled from `/proc` every 30 s into `out/plain-samples.csv`. | `python -m dawn_sim --no-core --no-timed --no-keyboard` in one terminal, then `SOAK_PORT=8081 .venv/bin/python docs/reliability/soak_driver.py` |
| `soak_core.py` | The same drive, with dawn-core in-process under tracemalloc: traced memory, reachable objects, tasks and the size of every bounded structure every 30 s (`out/samples.csv`), and a growth table by source line at the end (`out/tracemalloc.txt`). | with the simulator stack running: `.venv/bin/python docs/reliability/soak_core.py` |
| `soak_face.js` | The kiosk page in Chromium against a running core; heap, DOM nodes, listeners and documents over the DevTools protocol every 30 s after a forced GC. | `FACE_URL=http://127.0.0.1:8080/face SOAK_MINUTES=13 node docs/reliability/soak_face.js` |
| `run_hang.sh`, `hang_face.js` | End to end: a core process, an alarm two to three minutes out, core frozen with SIGSTOP 25 s before it; the face must ring by itself 45 s after the alarm and hand back once core is thawed. | with the simulator stack running: `bash docs/reliability/run_hang.sh` |
| `summarize.py` | First, last, min, max and second-half slope of every column of a soak CSV. | `python docs/reliability/summarize.py out/*.csv` |

All of the Python here runs in the project's `.venv` with `DAWN_SIM=1`; the soaks and the hang test expect the
simulator stack (`python -m dawn_sim --no-core --no-timed --no-keyboard`: the hub on 8099, the fake welle-cli on 8000,
the fake gpsd on 2947) and write their output under `docs/reliability/out/` (ignored by git) unless `SOAK_OUT` says
otherwise. The soaks patch the live config through the API, which writes to `config/config.sim.yaml`: restore it
with `git checkout -- config/config.sim.yaml` afterwards.
