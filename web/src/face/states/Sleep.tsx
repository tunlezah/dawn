import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { fmtShort, fmtTime, useNow } from '../../shared/time';
import { WeatherIcon } from '../../shared/icons/weather';
import { DEMO_WEATHER } from '../../shared/demo';
import { IconAlarm } from '../components/icons';
import { secondsOfDay, sleepSpot } from '../burnin';

const FADE_MS = 750;

/** Sleep mode: the clock alone, small and deep amber on black, with the next alarm and a weather icon under it.
 *  Every `sleep_jump_s` it fades out, moves and fades back in (burn-in): the place follows from the time itself,
 *  so a reload keeps it. With `sleep_screen_off` the screen stays black and the backlight is off until a tap. */
export function Sleep() {
  const s = useState();
  const now = useNow();
  const round = s.display.layout === 'round';
  const idx = Math.floor(secondsOfDay(now, s.tz) / Math.max(10, s.display.sleep_jump_s));
  const [shown, setShown] = useReactState(idx);
  const [visible, setVisible] = useReactState(true);
  useEffect(() => {
    if (idx === shown) return;
    setVisible(false);
    const t = window.setTimeout(() => { setShown(idx); setVisible(true); }, FADE_MS);
    return () => clearTimeout(t);
  }, [idx, shown]);

  // screen off: black, backlight off; a tap shows the clock for a few seconds (face.peek_until)
  if (s.display.sleep_screen_off && !(s.face.peek_until && new Date(s.face.peek_until) > now)) return <div className="f-sleep" data-testid="sleep" />;
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const w = DEMO_WEATHER ? { ...s.weather, available: true, ...DEMO_WEATHER } : s.weather;
  const next = s.alarms.next;
  // the alarm as a time; its weekday too when it is more than a day away
  const alarm = next ? (next.in_seconds < 86400 ? fmtShort(next.at, s.tz, s.settings.clock_24h)
    : `${new Intl.DateTimeFormat('en-AU', { timeZone: s.tz, weekday: 'short' }).format(new Date(next.at))} ${fmtShort(next.at, s.tz, s.settings.clock_24h)}`) : null;
  const p = sleepSpot(shown, round);
  return (
    <div className="f-sleep" data-testid="sleep" style={{ ['--lvl' as string]: `${s.display.sleep_level}%` }}>
      <div className={`f-sleep-clock ${visible ? '' : 'out'}`} style={{ left: `${p.x}%`, top: `${p.y}%` }}>
        <div className="t tnum">{t.hm}{t.ampm && <span className="ampm">{t.ampm}</span>}</div>
        {(alarm || w.available) && (
          <div className="a tnum">
            {alarm && <><IconAlarm /><span>{alarm}</span></>}
            {w.available && <WeatherIcon icon={w.icon} size={0} className="f-icon w" />}
          </div>
        )}
      </div>
    </div>
  );
}
