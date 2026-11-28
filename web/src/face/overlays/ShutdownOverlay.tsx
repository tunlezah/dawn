import { useState } from '../../shared/store';
import { fmtShort } from '../../shared/time';

/** The hold-to-shutdown countdown. With an alarm (or a nap) due within a day it says that it will not ring while Dawn is
 *  off, or, on a Pi 5 whose RTC can switch it on again, when it will come back. */
export function ShutdownOverlay({ seconds }: { seconds: number }) {
  const s = useState();
  const pending = pendingAlarm(s);
  const wake = pending && s.system.can_wake ? new Date(new Date(pending.at).getTime() - 10 * 60_000).toISOString() : null;
  return (
    <div className="face-overlay" style={{ background: 'rgba(0,0,0,0.85)', pointerEvents: 'auto' }}>
      <div className="text-center">
        <div className="f-sub">Shutting down in</div>
        <div className="face-time f-ambient-clock tnum">{seconds}</div>
        {pending && (
          <div className="f-sub mt-[2vmin]" data-testid="shutdown-alarm-warning" style={{ color: 'var(--warn)' }}>
            {wake ? `Dawn will switch itself on at ${fmtShort(wake, s.tz, s.settings.clock_24h)} for the ${fmtShort(pending.at, s.tz, s.settings.clock_24h)} ${pending.what}`
              : `The ${fmtShort(pending.at, s.tz, s.settings.clock_24h)} ${pending.what} will not ring while Dawn is off`}
          </div>
        )}
        <div className="f-tiny mt-[2vmin]">release to cancel</div>
      </div>
    </div>
  );
}

export function pendingAlarm(s: { alarms: { next: { at: string } | null }; timers: { nap: { ends_at: string } | null } }): { at: string; what: string } | null {
  const now = Date.now();
  const soon = (at: string) => { const t = new Date(at).getTime(); return t >= now && t - now <= 24 * 3600_000; };
  const c: { at: string; what: string }[] = [];
  if (s.alarms.next && soon(s.alarms.next.at)) c.push({ at: s.alarms.next.at, what: 'alarm' });
  if (s.timers.nap && soon(s.timers.nap.ends_at)) c.push({ at: s.timers.nap.ends_at, what: 'nap alarm' });
  c.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
  return c[0] ?? null;
}
