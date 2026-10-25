import os, signal, sys
os.environ["DAWN_SIM"] = "1"
from dawn_core.db.engine import Database
from dawn_core.db.models import AlarmRow
path, clean = sys.argv[1], sys.argv[2] == "clean"
db = Database(path)
with db.session() as s:
    s.add(AlarmRow(label="old alarm", time="06:30")); s.commit()
with db.engine.connect() as c:
    c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
with db.session() as s:
    s.add(AlarmRow(label="alarm set last night", time="06:45")); s.commit()
if clean:
    db.engine.dispose()   # a clean stop: SQLite checkpoints and removes -wal/-shm
    sys.exit(0)
os.kill(os.getpid(), signal.SIGKILL)  # a crash or the watchdog: nothing is checkpointed
