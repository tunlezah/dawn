import { useState } from '../../shared/store';
import { fmtDate, fmtDayTime, fmtTime, useNow } from '../../shared/time';
import { WeatherIcon } from '../../shared/icons/weather';
import { IconAlarm } from './icons';
import { SOURCE_LABEL } from './Header';
import { VolumeCell } from './ControlBar';
import { Scene } from './Scene';
import { DEMO_WEATHER } from '../../shared/demo';

/** Ambient clock: standby, and the idle view while something plays. Huge clock, date, place + weather, next alarm,
 *  and a status strip along the bottom (what is playing / time sources, alarm, volume). */
export function Ambient({ compact = false }: { compact?: boolean }) {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const w = DEMO_WEATHER ? { ...s.weather, available: true, stale: false, ...DEMO_WEATHER } : s.weather;
  const np = s.now_playing;
  const scene = s.display.scene && s.display.palette !== 'night';
  const live = (k: string) => s.time_sources.sources.some((x) => x.kind === k && x.live);
  const playing = np.source !== 'none';
  const next = s.alarms.next;
  const flowing = s.audio.audio_flowing || s.airplay.playing || s.bluetooth.playing;
  const place = w.location_label || s.settings.name;
  return (
    <div className={`f-ambient fade-in ${compact ? 'compact' : ''} ${scene ? 'scenic' : ''}`}>
      {scene
        ? <Scene now={now} tz={s.tz} icon={w.available ? w.icon : null} temperature={w.available ? w.temperature : null} sunrise={w.sunrise || s.display.sunrise} sunset={w.sunset || s.display.sunset} latitude={s.settings.latitude} lowCpu={s.display.low_cpu} />
        : <div className="f-horizon" />}
      <div className="face-time f-ambient-clock">
        {t.hm}
        {s.display.show_seconds && <span className="f-sub ml-[2vmin]" style={{ fontSize: '9vmin' }}>{t.sec}</span>}
        {t.ampm && <span className="f-sub ml-[2vmin]" style={{ fontSize: '8vmin' }}>{t.ampm}</span>}
      </div>
      <div className="f-date">{fmtDate(now, s.tz)}</div>
      {w.available && (
        <div className={`f-where ${w.stale ? 'opacity-60' : ''}`}>
          <span>{place}</span>
          <span className="f-sep" />
          <span className="tnum">{w.temperature !== null ? `${Math.round(w.temperature)}°` : '–'}</span>
          <WeatherIcon icon={w.icon} size={0} className="f-icon" />
          {w.t_min !== null && w.t_max !== null && <span className="f-tiny tnum ml-[2.4vmin]">{Math.round(w.t_min)}° / {Math.round(w.t_max)}°{w.stale ? ' · stale' : ''}</span>}
        </div>
      )}
      <hr className="f-hr" />
      <div className="f-next">
        <IconAlarm />
        {next ? <span>Next alarm <b className="tnum">{fmtDayTime(next.at, s.tz, s.settings.clock_24h)}</b>{next.label && <span className="f-meta ml-[1.6vmin]">{next.label}</span>}</span> : <span className="f-meta">No alarm set</span>}
      </div>
      <div className="f-bar">
        <div className="cell grow">
          {playing ? (
            <>
              <span className={`dot ${flowing ? 'dot-live' : 'dot-warn'}`} />
              <div className="label">
                <div className="top">{SOURCE_LABEL[np.source]}</div>
                <div className="bottom">{[np.title || np.station, np.artist || (np.title ? np.station : np.dls)].filter(Boolean).join('  •  ')}</div>
              </div>
            </>
          ) : (
            <div className="face-dots" title="time sources: green = live">
              <span className={`dot ${live('gps') || s.time_sources.gps.fix >= 2 ? 'dot-live' : 'dot-off'}`} />GPS
              <span className={`dot ${live('dab') || s.dab.sync ? 'dot-live' : 'dot-off'}`} />DAB
              <span className={`dot ${live('ntp') ? 'dot-live' : 'dot-off'}`} />NTP
              <span className={`dot ${s.system.network.online ? 'dot-live' : 'dot-off'}`} />NET
            </div>
          )}
        </div>
        <div className="vsep" />
        <div className="cell">
          <IconAlarm />
          <div className="label">
            <div className="top">Alarm</div>
            <div className="bottom">{next ? fmtDayTime(next.at, s.tz, s.settings.clock_24h) : (!s.display.sensor_found && s.display.mode === 'auto' ? 'Off · no light sensor' : 'Off')}</div>
          </div>
        </div>
        <div className="vsep" />
        <VolumeCell readOnly />
      </div>
    </div>
  );
}
