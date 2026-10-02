import { useState } from '../../shared/store';
import { fmtTime, useNow } from '../../shared/time';

export function Ringing() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const r = s.alarms.ringing;
  const snoozed = r?.snoozed_until && new Date(r.snoozed_until).getTime() > Date.now();
  return (
    <div className="f-ring face-screen fade-in">
      <div className="f-glow" />
      <div className={`face-time f-ambient-clock ${snoozed ? '' : 'pulse'}`}>{t.hm}{t.ampm && <span className="f-sub ml-[2vmin]" style={{ fontSize: '8vmin', color: 'inherit', opacity: 0.7 }}>{t.ampm}</span>}</div>
      <div className="f-label">{r?.label || 'Alarm'}{r?.fallback && <span className="f-meta ml-[2.4vmin]">chime fallback</span>}</div>
      <div className="f-hint">
        {snoozed ? `Snoozed until ${fmtTime(new Date(r!.snoozed_until!), s.tz, s.settings.clock_24h).hm}` : 'Tap anywhere to snooze · press the button to stop'}
      </div>
    </div>
  );
}
