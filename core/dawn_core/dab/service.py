"""DabService: welle-cli supervision, tuning, scan, DLS/MOT, logos, DAB source."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime

from sqlmodel import delete, select

from ..audio.service import AudioService
from ..audio.sources import PlayerSource
from ..config import DawnConfig
from ..context import DawnContext
from ..db.models import DabEnsembleRow, DabServiceRow
from ..services import Service
from ..state.ui import DabService as DabServiceState
from ..state.ui import NowPlaying, ScanProgress
from .logos import LogoCache, monogram_svg
from .scanner import scan, scan_order, scanned_at
from .welle import MuxInfo, ServiceInfo, WelleClient, norm_sid, welle_args

log = logging.getLogger("dawn.dab")


def snr_to_signal(snr: float | None) -> int | None:
    if snr is None:
        return None
    return int(max(0, min(100, (snr / 20.0) * 100)))


class TunerBusy(RuntimeError):
    """The tuner is an alarm's: a scan, a retune or a decoder restart by hand has to wait."""


class DabSource(PlayerSource):
    kind = "dab"

    def __init__(self, player, url: str, svc: ServiceInfo, dab: DabService):
        super().__init__(player, url)
        self.svc = svc
        self.dab = dab
        self.label = svc.label
        self.ref = f"dab:{svc.sid}"
        self._channel: str | None = None
        self._reload: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.dab.ensure_tuned_for(self.svc.sid)
        self._channel = self.dab.channel
        await super().start()
        self.dab.mark_active(self.svc.sid)

    async def stop(self) -> None:
        self._cancel_reload()
        await super().stop()
        self.dab.mark_active(None)

    async def pause(self) -> None:
        self._cancel_reload()
        await super().pause()

    async def resume(self) -> None:
        if self._started and self.dab.channel != self._channel:
            # something (an alarm on another station) retuned welle while this was paused, so its stream is gone.
            # Tuning takes seconds: do it outside the arbiter's lock.
            self._cancel_reload()
            self._reload = asyncio.create_task(self._retune(), name=f"dab-resume-{self.svc.sid}")
            return
        await super().resume()

    async def _retune(self) -> None:
        try:
            await self.dab.ensure_tuned_for(self.svc.sid)
            self._channel = self.dab.channel
            if self._started:
                await self.player.load(self.url)
                self.dab.mark_active(self.svc.sid)
        except Exception as e:  # noqa: BLE001
            log.warning("could not resume %s after a retune: %s", self.svc.label, e)

    def _cancel_reload(self) -> None:
        if self._reload and not self._reload.done() and self._reload is not asyncio.current_task():
            self._reload.cancel()
        self._reload = None

    @property
    def flowing(self) -> bool:
        return super().flowing and self.dab.live_sync

    def now_playing(self) -> NowPlaying:
        live = self.dab.live.get(self.svc.sid)
        return NowPlaying(
            source="dab",
            station=self.svc.label,
            station_sid=self.svc.sid,
            title=None,
            dls=live.dls if live else None,
            logo_url=self.dab.logos.url(self.svc.sid),
            slide_url=(f"/api/dab/slide/{self.svc.sid}?v={live.mot_lastchange}" if live and live.mot_lastchange else None),
            signal=snr_to_signal(self.dab.snr),
            codec=self.svc.codec,
            bitrate=self.svc.bitrate,
        )


class DabService(Service):
    name = "dab"

    def __init__(self, ctx: DawnContext):
        self.ctx = ctx
        self.client = WelleClient(ctx.config.dab.welle_url)
        self.logos = LogoCache(ctx.data_dir)
        self.live: dict[str, ServiceInfo] = {}
        self.live_sync = False
        self.snr: float | None = None
        self.channel: str | None = None
        self.ensemble: str | None = None
        self.active_sid: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._scan_stop = asyncio.Event()
        self._last_restart = 0.0
        self._slide_seen: dict[str, int] = {}
        self._unit_check_at = 0.0
        self._initial_tune_done = False
        self._channel_check_at = 0.0
        # diagnostics: the latest mux as read, when, welle's own messages, and the FIC CRC error rate
        self.last_mux: MuxInfo | None = None
        self.last_mux_at: float | None = None
        self.messages: deque[tuple[str, str]] = deque(maxlen=200)
        self.arg_notes: list[str] = []
        self.fic_errors_per_min: float | None = None
        self._crc_prev: tuple[float, int] | None = None
        # per service: (time, frame, rs, aac) at the start of the window, and the last rates per minute
        self._err_prev: dict[str, tuple[float, int, int, int]] = {}
        self.error_rates: dict[str, dict[str, float]] = {}

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self.ctx.svc(AudioService).register_factory("dab", self.make_source)
        self.channel = self.ctx.db.get("dab.last_channel") or self.ctx.config.dab.default_channel
        self._write_env()
        self._task = asyncio.create_task(self._loop(), name="dab-loop")
        self.publish()

    async def stop(self) -> None:
        for t in (self._task, self._scan_task):
            if t:
                t.cancel()
        await self.client.close()

    async def on_config(self, old: DawnConfig, new: DawnConfig) -> None:
        if old.dab.welle_url != new.dab.welle_url:
            await self.client.close()
            self.client = WelleClient(new.dab.welle_url)
        if old.dab.welle_args != new.dab.welle_args or old.dab.gain != new.dab.gain:
            self._write_env()
        self.publish()

    def _write_env(self) -> None:
        """Environment file read by dawn-dab.service so welle restarts on the last channel."""
        cfg = self.ctx.config.dab
        args, notes = welle_args(list(cfg.welle_args), cfg.gain)
        if notes != self.arg_notes:
            for note in notes:
                log.warning("dab.welle_args: %s", note)
            self.arg_notes = notes
        try:
            (self.ctx.data_dir / "dab.env").write_text(f"DAWN_DAB_CHANNEL={self.channel}\nDAWN_WELLE_ARGS={' '.join(args)}\n")
        except OSError as e:
            log.warning("cannot write dab.env: %s", e)

    # ---- polling ---------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await self._poll()
            except Exception:  # noqa: BLE001
                log.exception("dab poll failed")
            fast = self.active_sid is not None
            await asyncio.sleep(self.ctx.config.dab.poll_interval_s if fast else 5.0)

    async def _poll(self) -> None:
        if self._scan_task and not self._scan_task.done():
            return
        st = self.ctx.store.state.dab
        m = await self.client.mux()
        st.available = m is not None
        if self.ctx.sim:
            # the fake welle answers 503 when the sim hub says the SDR is unplugged
            st.sdr_present = m is not None
            st.tuner = "Rafael Micro R828D (sim)" if m is not None else None
            self.ctx.store.state.system.sdr_present = st.sdr_present
            self.ctx.store.state.system.sdr_tuner = st.tuner
        if m is None:
            self.live_sync = False
            self.snr = None
            st.sync = False
            st.snr = None
        else:
            self._diag(m)
            if not m.channel and (not self._initial_tune_done or time.time() - self._channel_check_at > 10):
                # upstream mux.json has no channel; welle answers it on GET /channel
                self._channel_check_at = time.time()
                m.channel = await self.client.channel()
            self.live_sync = m.sync
            self.snr = m.snr
            st.sync = m.sync
            st.snr = m.snr
            if m.channel:
                ch = str(m.channel).upper()
                if ch != self.channel and not self._initial_tune_done and self.channel:
                    # boot: welle is on a different channel than the one we last used; retune it
                    self._initial_tune_done = True
                    log.info("retuning welle from %s to last-used %s", ch, self.channel)
                    await self.tune(self.channel)
                    return
                self._initial_tune_done = True
                if ch != self.channel:
                    self.channel = ch
            if m.sync:
                self.ensemble = m.ensemble_label or self.ensemble
                for s in m.services:
                    prev = self.live.get(s.sid)
                    self.live[s.sid] = s
                    if s.mot_lastchange and s.mot_lastchange != self._slide_seen.get(s.sid):
                        await self._fetch_slide(s.sid, s.mot_lastchange)
                    if prev is None or prev.dls != s.dls:
                        self._metadata_changed(s.sid)
        now = time.time()
        if not self.ctx.sim and now - self._unit_check_at > 10:
            self._unit_check_at = now
            st.service_state = await self._unit_state()
        st.channel = self.channel
        st.ensemble = self.ensemble if self.live_sync else None
        st.enabled = self.ctx.config.dab.enabled
        self.ctx.store.touch()

    def _diag(self, m: MuxInfo) -> None:
        now = time.time()
        self.last_mux, self.last_mux_at = m, now
        stamp = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
        for line in m.messages:
            self.messages.append((stamp, line))
        if m.fic_crc_errors is not None:
            if self._crc_prev and m.fic_crc_errors >= self._crc_prev[1] and now - self._crc_prev[0] >= 20:
                self.fic_errors_per_min = round((m.fic_crc_errors - self._crc_prev[1]) * 60 / (now - self._crc_prev[0]), 1)
                self._crc_prev = (now, m.fic_crc_errors)
            elif not self._crc_prev or m.fic_crc_errors < self._crc_prev[1]:  # first read, or welle restarted
                self._crc_prev = (now, m.fic_crc_errors)
        for svc in m.services:
            if svc.frame_errors is None or not svc.decoding:
                continue
            cur = (now, svc.frame_errors or 0, svc.rs_errors or 0, svc.aac_errors or 0)
            prev = self._err_prev.get(svc.sid)
            if prev is None or any(c < p for c, p in zip(cur[1:], prev[1:], strict=True)):
                self._err_prev[svc.sid] = cur
            elif now - prev[0] >= 20:
                k = 60 / (now - prev[0])
                self.error_rates[svc.sid] = {"frame": round((cur[1] - prev[1]) * k, 1), "rs": round((cur[2] - prev[2]) * k, 1),
                                             "aac": round((cur[3] - prev[3]) * k, 1), "at": now}
                self._err_prev[svc.sid] = cur

    async def fresh_mux(self) -> MuxInfo | None:
        """A read of mux.json right now (Diagnostics' live meter). welle drains its message log on every read,
        so every read goes through _diag."""
        m = await self.client.mux()
        if m is not None:
            self._diag(m)
            m.channel = m.channel or self.channel
        return m

    def _metadata_changed(self, sid: str) -> None:
        if sid == self.active_sid:
            self.ctx.svc(AudioService).publish()

    async def _fetch_slide(self, sid: str, lastchange: int) -> None:
        got = await self.client.slide(sid)
        if got:
            data, ctype = got
            self.logos.store(sid, data, ctype)
            self._slide_seen[sid] = lastchange
            with self.ctx.db.session() as s:
                for row in s.exec(select(DabServiceRow).where(DabServiceRow.sid == sid)).all():
                    row.has_slide = True
                    s.add(row)
                s.commit()
            self._metadata_changed(sid)

    async def _unit_state(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec("systemctl", "is-active", self.ctx.config.dab.service_name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 3)
            return out.decode().strip() or "unknown"
        except (TimeoutError, OSError):
            return "unknown"

    # ---- tuning / source -------------------------------------------------
    def service_row(self, sid: str) -> DabServiceRow | None:
        """The scanned station, or None (also when the database cannot be read: the live ensemble may still know it)."""
        sid = norm_sid(sid)
        try:
            with self.ctx.db.session() as s:
                return s.exec(select(DabServiceRow).where(DabServiceRow.sid == sid)).first()
        except Exception as e:  # noqa: BLE001
            log.warning("cannot look up station %s: %s", sid, e)
            return None

    def _svc_info(self, sid: str) -> ServiceInfo:
        sid = norm_sid(sid)
        live = self.live.get(sid)
        if live:
            return live
        row = self.service_row(sid)
        if row:
            return ServiceInfo(sid=sid, label=row.label, short_label=row.short_label, bitrate=row.bitrate, codec=row.codec, pty=row.pty)
        return ServiceInfo(sid=sid, label=f"DAB {sid.upper()}")

    async def make_source(self, arg: str, level: str = "user"):
        sid = norm_sid(arg)
        svc = self._svc_info(sid)
        audio = self.ctx.svc(AudioService)
        player = await audio.player(audio.player_name("dab", level))
        return DabSource(player, self.client.stream_url(svc), svc, self)

    async def ensure_tuned_for(self, sid: str) -> None:
        cfg = self.ctx.config.dab
        if self._scan_task and not self._scan_task.done():
            # a scan retunes every few seconds: the station that is wanted now comes first
            log.warning("stopping the scan to play %s", sid)
            self._scan_stop.set()
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(self._scan_task), 8)
        row = self.service_row(sid)
        want = row.channel.upper() if row else None
        if want is None:
            if sid in self.live:
                return
            raise RuntimeError(f"service {sid} unknown; run a scan first")
        if want != self.channel or not self.live_sync:
            await self.tune(want)
        deadline = time.monotonic() + cfg.sync_timeout_s
        while time.monotonic() < deadline:
            m = await self.client.mux()
            if m and m.sync:
                self.live_sync = True
                for s in m.services:
                    self.live[s.sid] = s
                if sid in self.live:
                    return
            await asyncio.sleep(0.5)
        if not self.live_sync:
            raise RuntimeError(f"no DAB sync on channel {want}")

    async def tune(self, channel: str) -> None:
        channel = channel.upper()
        if not await self.client.set_channel(channel):
            raise RuntimeError("welle-cli unreachable")
        self.channel = channel
        self.live.clear()
        self.live_sync = False
        self.ctx.db.try_set("dab.last_channel", channel)  # a read-only database must not undo a retune
        self._write_env()
        self.publish()

    def mark_active(self, sid: str | None) -> None:
        self.active_sid = sid

    def _alarm_conflict(self, within_s: float = 0.0, *, ringing_only: bool = False) -> str | None:
        try:
            from ..alarms.service import AlarmService

            return self.ctx.svc(AlarmService).dab_conflict(within_s, ringing_only=ringing_only)
        except (ImportError, KeyError):
            return None

    async def user_tune(self, channel: str) -> None:
        """A retune asked for by hand (the API, Diagnostics): not while an alarm on the radio rings or is snoozed."""
        hold = self._alarm_conflict(ringing_only=True)
        if hold:
            raise TunerBusy(f"{hold}; retune after it")
        await self.tune(channel)

    async def prepare_for(self, sid: str, *, may_retune: bool) -> tuple[str, str]:
        """Get the tuner ready for an alarm on `sid` minutes ahead. Returns (status, what is wrong): "ok"; "tuning"
        (just retuned, or a scan is being stopped: sync follows); "busy" (someone is listening on another channel,
        which the alarm retunes when it rings); "fail"."""
        sid = norm_sid(sid)
        if not self.ctx.store.state.dab.sdr_present:
            return "fail", "no SDR"
        if self._scan_task and not self._scan_task.done():
            log.warning("a scan is running with an alarm on DAB minutes away; stopping it")
            self._scan_stop.set()
            return "tuning", "stopping a scan"
        row = self.service_row(sid)
        want = row.channel.upper() if row else None
        if want is None and sid not in self.live:
            return "fail", "station unknown"
        if not await self.client.reachable():
            await self.restart_welle(reason="alarm")
            return "fail", "DAB decoder not answering"
        if want and want != self.channel:
            if not may_retune:
                return "busy", f"the radio is playing on {self.channel}; it retunes to {want} when the alarm rings"
            log.info("retuning to %s for an alarm", want)
            try:
                await self.tune(want)
            except RuntimeError as e:
                return "fail", str(e)
            return "tuning", f"tuned to {want}"
        m = await self.client.mux()
        if m is None:
            return "fail", "DAB decoder not answering"
        if not m.sync:
            return "fail", f"no DAB signal on {self.channel}"
        self.live_sync = True
        for s in m.services:
            self.live[s.sid] = s
        if sid not in {s.sid for s in m.services}:
            return "fail", "station not on air in its ensemble"
        return "ok", ""

    async def restart_welle(self, force: bool = False, reason: str = "unreachable") -> bool:
        """Restart the dawn-dab unit (at most once per 30 s unless forced from Diagnostics). Returns True if attempted.
        A restart by hand (`reason="manual"`) waits while an alarm on the radio rings."""
        if reason == "manual":
            hold = self._alarm_conflict(ringing_only=True)
            if hold:
                raise TunerBusy(f"{hold}; restart the decoder after it")
        now = time.monotonic()
        if now - self._last_restart < (5 if force else 30):
            return False
        self._last_restart = now
        self.ctx.db.log_event("dab_restart", channel=self.channel, reason=reason)
        if self.ctx.sim:
            log.warning("sim: would restart %s", self.ctx.config.dab.service_name)
            return True
        proc = await asyncio.create_subprocess_exec(self.ctx.config.system.sudo_binary, "-n", "systemctl", "restart", self.ctx.config.dab.service_name)
        await proc.wait()
        return True

    async def healthy(self) -> bool:
        return self.ctx.store.state.dab.sdr_present and await self.client.reachable()

    # ---- scan ------------------------------------------------------------
    async def start_scan(self) -> bool:
        if self._scan_task and not self._scan_task.done():
            return False
        cfg = self.ctx.config.dab
        # a scan holds the tuner for minutes: not into an alarm on the radio, nor its preparation
        hold = self._alarm_conflict(len(scan_order(cfg.scan_channels, cfg.scan_priority)) * cfg.scan_dwell_s + 3 * cfg.scan_signal_wait_s + 30)
        if hold:
            raise TunerBusy(f"{hold}; scanning waits until it has rung")
        audio = self.ctx.svc(AudioService)
        for level in ("user", "sleep"):
            slot = audio.arbiter.slot(level)
            if slot and slot.source.kind == "dab":
                await audio.stop_level(level)
        alarm_slot = audio.arbiter.slot("alarm")
        if alarm_slot and alarm_slot.source.kind == "dab":
            raise RuntimeError("cannot scan while a DAB alarm is ringing")
        self._scan_stop.clear()
        self._scan_task = asyncio.create_task(self._run_scan(), name="dab-scan")
        return True

    async def cancel_scan(self) -> None:
        self._scan_stop.set()

    async def _run_scan(self) -> None:
        cfg = self.ctx.config.dab
        st = self.ctx.store.state.dab
        before = self.channel
        channels = scan_order(cfg.scan_channels, cfg.scan_priority)
        st.scan = ScanProgress(running=True, total=len(channels), started_at=self.ctx.store.iso())
        self.ctx.store.touch()

        async def progress(i: int, total: int, ch: str, n_svc: int, n_ens: int) -> None:
            st.scan.index, st.scan.total, st.scan.channel, st.scan.found_services, st.scan.found_ensembles = i, total, ch or None, n_svc, n_ens
            self.ctx.store.touch()

        try:
            found = await scan(self.client, channels, cfg.scan_dwell_s, progress, self._scan_stop, cfg.scan_signal_wait_s)
            if found:
                self._store_scan(found)
            st.last_scan_at = scanned_at()
            self.ctx.db.set("dab.last_scan_at", st.last_scan_at)
            self.ctx.db.log_event("dab_scan", ensembles=len(found), services=sum(len(m.services) for m in found.values()))
        except Exception:  # noqa: BLE001
            log.exception("scan failed")
        finally:
            st.scan.running = False
            st.scan.channel = None
            target = before or (next(iter(found)) if found else None) or cfg.default_channel
            try:
                await self.tune(target)
            except Exception:  # noqa: BLE001
                log.warning("could not retune to %s after scan", target)
            self.publish()

    def _store_scan(self, found: dict[str, MuxInfo]) -> None:
        now = datetime.now(UTC)
        with self.ctx.db.session() as s:
            s.exec(delete(DabEnsembleRow))  # type: ignore[arg-type]
            s.exec(delete(DabServiceRow))  # type: ignore[arg-type]
            for ch, m in found.items():
                s.add(DabEnsembleRow(channel=ch, eid=m.ensemble_id, label=m.ensemble_label, snr=m.snr, scanned_at=now))
                for svc in m.services:
                    s.add(
                        DabServiceRow(
                            sid=svc.sid, channel=ch, ensemble_eid=m.ensemble_id, ensemble_label=m.ensemble_label, label=svc.label,
                            short_label=svc.short_label, bitrate=svc.bitrate, codec=svc.codec, pty=svc.pty,
                            signal=snr_to_signal(m.snr), has_slide=self.logos.path_for(svc.sid) is not None, scanned_at=now,
                        )
                    )
            s.commit()

    # ---- queries ---------------------------------------------------------
    def services(self) -> list[DabServiceState]:
        with self.ctx.db.session() as s:
            rows = s.exec(select(DabServiceRow).order_by(DabServiceRow.channel, DabServiceRow.label)).all()  # type: ignore[attr-defined]
        return [
            DabServiceState(
                sid=r.sid, label=r.label, short_label=r.short_label, ensemble=r.ensemble_label, ensemble_id=r.ensemble_eid, channel=r.channel,
                bitrate=r.bitrate, codec=r.codec, pty=r.pty, logo_url=self.logos.url(r.sid), signal=r.signal, has_slide=self.logos.path_for(r.sid) is not None,
            )
            for r in rows
        ]

    def ensembles(self) -> list[dict]:
        with self.ctx.db.session() as s:
            rows = s.exec(select(DabEnsembleRow).order_by(DabEnsembleRow.channel)).all()  # type: ignore[attr-defined]
        return [{"channel": r.channel, "eid": r.eid, "label": r.label, "snr": r.snr, "scanned_at": r.scanned_at.isoformat()} for r in rows]

    def logo(self, sid: str) -> tuple[bytes, str]:
        sid = norm_sid(sid)
        p = self.logos.path_for(sid)
        if p:
            ctype = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}[p.suffix[1:]]
            return p.read_bytes(), ctype
        return monogram_svg(sid, self._svc_info(sid).label).encode(), "image/svg+xml"

    def publish(self) -> None:
        st = self.ctx.store.state.dab
        st.services = self.services()
        st.channel = self.channel
        st.last_scan_at = self.ctx.db.get("dab.last_scan_at")
        self.ctx.store.touch()
