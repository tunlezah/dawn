import { useState } from '../../shared/store';
import { fmtDate, fmtTime, useNow, fmtDayTime } from '../../shared/time';
import { WeatherIcon } from '../../shared/icons/weather';

export function Standby() {
  const s = useState();
  const now = useNow(s.display.low_cpu ? 1000 : 1000);
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const w = s.weather;
  return (
    <div className="face-screen fade-in">
      <div className="flex items-start justify-between">
        <div className="face-dots">
          <span className={`dot ${s.time_sources.gps.fix >= 2 ? 'dot-ok' : 'dot-off'}`} />GPS
          <span className={`dot ${s.dab.sync ? 'dot-ok' : 'dot-off'}`} />DAB
          <span className={`dot ${s.system.network.online ? 'dot-ok' : 'dot-off'}`} />NET
        </div>
        {w.available && (
          <div className={`flex items-center gap-[2vmin] ${w.stale ? 'opacity-60' : ''}`}>
            <WeatherIcon icon={w.icon} size={0} className="w-[9vmin] h-[9vmin]" />
            <div className="leading-none">
              <div className="text-[6vmin] font-semibold tnum">{w.temperature !== null ? Math.round(w.temperature) : '–'}°</div>
              {w.t_min !== null && w.t_max !== null && <div className="text-[3vmin] text-muted tnum mt-[0.8vmin]">{Math.round(w.t_min)}° / {Math.round(w.t_max)}°{w.stale ? ' · stale' : ''}</div>}
            </div>
          </div>
        )}
      </div>
      <div className="flex-1 flex flex-col justify-center">
        <div className="face-time huge flex items-baseline">
          <span>{t.hm}</span>
          {s.display.show_seconds && <span className="text-[9vmin] text-muted ml-[2vmin]">{t.sec}</span>}
          {t.ampm && <span className="text-[8vmin] text-muted ml-[2vmin]">{t.ampm}</span>}
        </div>
        <div className="face-date mt-[2vmin]">{fmtDate(now, s.tz)}</div>
      </div>
      <div className="flex items-end justify-between">
        {s.alarms.next ? (
          <div className="face-chip"><span>⏰</span><span className="tnum">{fmtDayTime(s.alarms.next.at, s.tz, s.settings.clock_24h)}</span><span className="opacity-70">{s.alarms.next.label}</span></div>
        ) : <div className="face-chip opacity-60">No alarm</div>}
        {!s.display.sensor_found && s.display.mode === 'auto' && <div className="face-chip opacity-60">sensor not found</div>}
      </div>
    </div>
  );
}
