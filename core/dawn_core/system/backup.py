"""Backup/restore of config.yaml + database contents as one JSON document."""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import delete, select

from .. import __version__
from ..config import DawnConfig
from ..context import DawnContext
from ..db.models import KV, AlarmRow, DabEnsembleRow, DabServiceRow, PresetRow

log = logging.getLogger("dawn.backup")

TABLES = {"alarms": AlarmRow, "presets": PresetRow, "dab_services": DabServiceRow, "dab_ensembles": DabEnsembleRow}


def make_backup(ctx: DawnContext) -> dict[str, Any]:
    out: dict[str, Any] = {"dawn_backup": 1, "version": __version__, "created_at": ctx.store.iso(), "config": ctx.config.model_dump(mode="json"), "db": {}}
    with ctx.db.session() as s:
        for name, model in TABLES.items():
            out["db"][name] = [r.model_dump(mode="json") for r in s.exec(select(model)).all()]
        out["db"]["kv"] = {r.key: r.value for r in s.exec(select(KV)).all() if not r.key.startswith("auth.")}
    return out


async def restore_backup(ctx: DawnContext, doc: dict[str, Any]) -> dict[str, int]:
    if doc.get("dawn_backup") != 1:
        raise ValueError("not a Dawn backup document")
    cfg = DawnConfig.model_validate(doc.get("config") or {})
    counts: dict[str, int] = {}
    with ctx.db.session() as s:
        for name, model in TABLES.items():
            rows = doc.get("db", {}).get(name)
            if rows is None:
                continue
            s.exec(delete(model))  # type: ignore[arg-type]
            for r in rows:
                r = dict(r)
                for k in ("created_at", "updated_at", "scanned_at"):
                    r.pop(k, None)
                s.add(model(**r))
            counts[name] = len(rows)
        kv = doc.get("db", {}).get("kv")
        if isinstance(kv, dict):
            for k, v in kv.items():
                if k.startswith("auth."):
                    continue
                row = s.get(KV, k)
                if row is None:
                    s.add(KV(key=k, value=str(v)))
                else:
                    row.value = str(v)
                    s.add(row)
            counts["kv"] = len(kv)
        s.commit()
    await ctx.cfg_mgr.replace(cfg.model_dump(mode="json"))
    ctx.db.log_event("restore", **counts)
    # services re-read their data on publish
    from ..alarms.service import AlarmService
    from ..audio.service import AudioService

    try:
        ctx.svc(AlarmService).publish(force=True)
        ctx.svc(AudioService).publish()
    except Exception:  # noqa: BLE001
        log.exception("post-restore publish failed")
    return counts
