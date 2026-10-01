import { useState } from '../../shared/store';
import { fmtDuration, fmtTime, useNow } from '../../shared/time';

export function Countdown() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const timer = s.timers.nap ?? s.timers.sleep;
  if (!timer) return null;
  const remaining = Math.max(0, (new Date(timer.ends_at).getTime() - now.getTime()) / 1000);
  const pct = timer.total_s ? Math.max(0, Math.min(100, (remaining / timer.total_s) * 100)) : 0;
  return (
    <div className="face-screen fade-in">
      <div className="flex items-center justify-between">
        <div className="face-chip">{timer.kind === 'nap' ? 'Nap' : 'Sleep'}{timer.fading ? ' · fading' : ''}</div>
        <div className="face-time small tnum">{t.hm}</div>
      </div>
      <div className="flex-1 flex flex-col items-center justify-center">
        <div className="face-time big tnum">{fmtDuration(remaining)}</div>
        <div className="vol-bar mt-[4vmin]"><div style={{ width: `${pct}%` }} /></div>
      </div>
      <div className="face-dots justify-center text-muted">{timer.kind === 'nap' ? 'Rings with the chime when done' : (s.now_playing.station || s.now_playing.title || 'Playing')} · tap for menu</div>
    </div>
  );
}
