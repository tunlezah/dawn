#!/usr/bin/env bash
# End-to-end: core as its own process against the running sim stack; an alarm ~2-3 minutes out; core frozen with
# SIGSTOP 25 s before it; the face must ring by itself and hand back when core thaws.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/out"
mkdir -p "$OUT"
cd "$ROOT"
DAWN_SIM=1 DAWN_CONFIG=$ROOT/config/config.sim.yaml DAWN_DATA_DIR=$ROOT/var/dawn-hang DAWN_RUNTIME_DIR=$ROOT/var/run-hang PYTHONUNBUFFERED=1 \
  nohup .venv/bin/python -m dawn_core > "$OUT/hang-core.log" 2>&1 &
CORE_PID=$!
echo "core pid $CORE_PID"
for _ in $(seq 1 60); do curl -sf localhost:8080/api/health >/dev/null 2>&1 && break; sleep 1; done
curl -s -X POST localhost:8099/set -H 'content-type: application/json' -d '{"audio_flowing": true, "sdr_present": true}' >/dev/null
# the alarm: the next full minute at least 130 s away, in the clock's own zone
ALARM_AT="$(.venv/bin/python - <<'EOF'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
tz = ZoneInfo("Australia/Sydney")
t = datetime.now(tz) + timedelta(seconds=130)
t = (t + timedelta(minutes=1)).replace(second=0, microsecond=0)
print(t.isoformat())
EOF
)"
HHMM="${ALARM_AT:11:5}"
echo "alarm at $ALARM_AT ($HHMM)"
curl -s -X POST localhost:8080/api/alarms -H 'content-type: application/json' \
  -d "{\"label\": \"Hang test\", \"time\": \"$HHMM\", \"repeat\": \"daily\", \"source\": \"chime:birds\", \"ramp_seconds\": 0}" | head -c 200; echo
CORE_PID=$CORE_PID ALARM_AT="$ALARM_AT" node "$HERE/hang_face.js"
RC=$?
echo "scenario exit $RC"
kill -CONT $CORE_PID 2>/dev/null
kill $CORE_PID 2>/dev/null
sleep 2
kill -9 $CORE_PID 2>/dev/null
exit $RC
