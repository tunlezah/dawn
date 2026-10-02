import { useEffect, useRef, useState as useReactState } from 'react';
import type { PointerEvent } from 'react';
import { useState } from '../../shared/store';
import { actions } from '../../shared/api';
import { fmtTime, useNow } from '../../shared/time';

/** Hold the screen this long to stop the alarm. A tap snoozes, so snooze and stop stay on different gestures
 *  (the big button, when fitted, still stops with a short press). */
export const STOP_HOLD_MS = 2000;
const run = (p: Promise<unknown>) => p.catch(() => {});

export function Ringing() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const r = s.alarms.ringing;
  const snoozed = r?.snoozed_until && new Date(r.snoozed_until).getTime() > Date.now();

  const [holding, setHolding] = useReactState(false);
  const timer = useRef<number | null>(null);
  const active = useRef(false);
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    active.current = true;
    setHolding(true);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      active.current = false;
      setHolding(false);
      run(actions.stopRinging());
    }, STOP_HOLD_MS);
  };
  const onUp = () => {
    if (!active.current) return;
    active.current = false;
    setHolding(false);
    if (timer.current !== null) { clearTimeout(timer.current); timer.current = null; run(actions.snooze()); }
  };
  useEffect(() => () => { if (timer.current !== null) clearTimeout(timer.current); }, []);

  const hint = holding ? 'Keep holding to stop' : snoozed ? `Snoozed until ${fmtTime(new Date(r!.snoozed_until!), s.tz, s.settings.clock_24h).hm} · hold to stop` : 'Tap anywhere to snooze · hold to stop';
  return (
    <div className="f-ring face-screen fade-in" onPointerDown={onDown} onPointerUp={onUp} onPointerCancel={onUp}>
      <div className="f-glow" />
      <div className={`face-time f-ambient-clock ${snoozed ? '' : 'pulse'}`}>{t.hm}{t.ampm && <span className="f-sub ml-[2vmin]" style={{ fontSize: '8vmin', color: 'inherit', opacity: 0.7 }}>{t.ampm}</span>}</div>
      <div className="f-label">{r?.label || 'Alarm'}{r?.fallback && <span className="f-meta ml-[2.4vmin]">chime fallback</span>}</div>
      <div className="f-hint">{hint}</div>
      <div className={`f-holdbar ${holding ? 'holding' : ''}`}><div /></div>
    </div>
  );
}
