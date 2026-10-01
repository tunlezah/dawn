import { useState } from '../../shared/store';
import { fmtTime, useNow } from '../../shared/time';

export function Ringing() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const r = s.alarms.ringing;
  const snoozed = r?.snoozed_until && new Date(r.snoozed_until).getTime() > Date.now();
  return (
    <div className="face-screen fade-in items-center text-center justify-center">
      <div className={`face-time huge ${snoozed ? '' : 'pulse'} text-accent`}>{t.hm}{t.ampm && <span className="text-[8vmin] ml-[2vmin]">{t.ampm}</span>}</div>
      <div className="mt-[3vmin] text-[6vmin] font-medium">{r?.label || 'Alarm'}{r?.fallback && <span className="text-muted text-[3.5vmin] ml-[2vmin]">chime fallback</span>}</div>
      <div className="mt-[2vmin] text-[4.2vmin] text-muted">
        {snoozed ? `Snoozed until ${fmtTime(new Date(r!.snoozed_until!), s.tz, s.settings.clock_24h).hm}` : 'Tap anywhere to snooze · hold the button to stop'}
      </div>
    </div>
  );
}
