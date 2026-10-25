import os, sys, logging
logging.basicConfig(level=logging.ERROR, format="   log: %(message)s")
os.environ["DAWN_SIM"] = "1"
from dawn_core.db.engine import Database
from dawn_core.db.models import AlarmRow
d = sys.argv[1]
print("   files:", sorted(f for f in os.listdir(d) if f.startswith("dawn.db")))
db = Database(os.path.join(d, "dawn.db"))
print("   mode:", db.mode, "| open_error:", db.open_error)
try:
    with db.session() as s:
        print("   alarms read:", sorted(r.label for r in s.query(AlarmRow).all()))
except Exception as e:
    print("   READ FAILED:", type(e).__name__, e)
print("   try_set:", db.try_set("k", 1), "| write_errors:", db.write_errors, "| last:", (db.last_write_error or "")[:90])
db.log_event("alarm_fire", id=1)
print("   write_errors after log_event:", db.write_errors)
