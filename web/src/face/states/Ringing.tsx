import { useEffect, useRef, useState as useReactState } from 'react';
import type { PointerEvent } from 'react';
import { useState } from '../../shared/store';
import { actions } from '../../shared/api';
import { fmtTime, useNow } from '../../shared/time';

/** Hold the screen this long to stop the alarm. A tap snoozes, so snooze and stop stay on different gestures
 *  (the big button, when fitted, still stops with a short press). */
export const STOP_HOLD_MS = 2000;
const run = (p: Promise<unknown>) => p.catch(() => {});

/** A ring the face runs by itself while core is not answering (see backup.ts): snoozed and stopped right here. */
export interface LocalRingProps { label: string; snoozedUntil: number | null; onSnooze: () => void; onStop: () => void }

export function Ringing({ local }: { local?: LocalRingProps }) {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const r = local ? null : s.alarms.ringing;
  const snoozedAt = local ? local.snoozedUntil : r?.snoozed_until ? new Date(r.snoozed_until).getTime() : null;
  const snoozed = snoozedAt !== null && snoozedAt > Date.now();

  const [holding, setHolding] = useReactState(false);
  const timer = useRef<number | null>(null);
  const active = useRef(false);
  const stop = () => (local ? local.onStop() : run(actions.stopRinging()));
  const snooze = () => (local ? local.onSnooze() : run(actions.snooze()));
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    active.current = true;
    setHolding(true);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      active.current = false;
      setHolding(false);
      stop();
    }, STOP_HOLD_MS);
  };
  const onUp = () => {
    if (!active.current) return;
    active.current = false;
    setHolding(false);
    if (timer.current !== null) { clearTimeout(timer.current); timer.current = null; snooze(); }
  };
  useEffect(() => () => { if (timer.current !== null) clearTimeout(timer.current); }, []);

  // which sound is on: the alarm's own source needs no note; anything below it is said, so a chime or the backup tone
  // in the morning is not a mystery
  const note = local ? 'backup alarm · Dawn is not responding' : r?.tier === 'buzzer' ? 'backup tone' : r?.fallback ? 'chime fallback' : null;
  const hint = holding ? 'Keep holding to stop' : snoozed ? `Snoozed until ${fmtTime(new Date(snoozedAt!), s.tz, s.settings.clock_24h).hm} · hold to stop` : 'Tap anywhere to snooze · hold to stop';
  return (
    <div className={`f-ring face-screen fade-in ${local ? 'local' : ''}`} onPointerDown={onDown} onPointerUp={onUp} onPointerCancel={onUp}>
      <div className="f-glow" />
      <div className={`face-time f-ambient-clock ${snoozed ? '' : 'pulse'}`}>{t.hm}{t.ampm && <span className="f-ampm" style={{ color: 'inherit', opacity: 0.7 }}>{t.ampm}</span>}</div>
      <div className="f-label">{local ? local.label : r?.label || 'Alarm'}{note && <span className="f-meta ml-[2.4vmin]">{note}</span>}</div>
      <div className="f-hint">{hint}</div>
      <div className={`f-holdbar ${holding ? 'holding' : ''}`}><div /></div>
    </div>
  );
}
