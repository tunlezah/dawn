import { useState } from '../../shared/store';
import { fmtDuration, useNow } from '../../shared/time';
import { FaceHeader } from '../components/Header';
import { VolumeCell } from '../components/ControlBar';
import { IconMoon, IconNap } from '../components/icons';

export function Countdown() {
  const s = useState();
  const now = useNow();
  const timer = s.timers.nap ?? s.timers.sleep;
  if (!timer) return null;
  const remaining = Math.max(0, (new Date(timer.ends_at).getTime() - now.getTime()) / 1000);
  const pct = timer.total_s ? Math.max(0, Math.min(100, (remaining / timer.total_s) * 100)) : 0;
  const nap = timer.kind === 'nap';
  return (
    <div className="face-screen fade-in">
      <FaceHeader left={<span className="face-chip">{nap ? <IconNap /> : <IconMoon />}{nap ? 'Nap' : 'Sleep'}{timer.fading ? ' · fading' : ''}</span>} />
      <div className="f-body" style={{ flexDirection: 'column', justifyContent: 'center', gap: '3vmin' }}>
        <div className="face-time f-ambient-clock compact tnum" style={{ fontSize: '28vmin' }}>{fmtDuration(remaining)}</div>
        <div className="vol-bar"><div style={{ width: `${pct}%` }} /></div>
        <div className="f-meta">{nap ? 'Rings with the chime when done' : `${s.now_playing.station || s.now_playing.title || 'Playing'} fades out`} · tap for menu</div>
      </div>
      <div className="f-bar">
        <div className="cell grow"><div className="label"><div className="top">{nap ? 'Nap timer' : 'Sleep timer'}</div><div className="bottom">ends {new Intl.DateTimeFormat('en-AU', { timeZone: s.tz, hour: '2-digit', minute: '2-digit', hour12: !s.settings.clock_24h }).format(new Date(timer.ends_at))}</div></div></div>
        <div className="vsep" />
        <VolumeCell readOnly />
      </div>
    </div>
  );
}
