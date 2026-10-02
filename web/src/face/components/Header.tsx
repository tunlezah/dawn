import type { ReactNode } from 'react';
import { useState } from '../../shared/store';
import { fmtTime, useNow } from '../../shared/time';
import { IconAirplay, IconBluetooth, IconRadio, SignalBars } from './icons';
import type { SourceKind } from '../../shared/types';

/** Header slot shared by every screen: status on the left, the clock always in the same place on the right. */
export function FaceHeader({ left }: { left?: ReactNode }) {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  return (
    <div className="f-header">
      <div className="f-status">{left}</div>
      <div className="face-time f-clock tnum">{t.hm}{t.ampm && <span className="f-meta ml-[1.2vmin]">{t.ampm}</span>}</div>
    </div>
  );
}

export const SOURCE_LABEL: Record<SourceKind, string> = { dab: 'DAB', airplay: 'AirPlay', bluetooth: 'Bluetooth', url: 'Stream', playlist: 'Playlist', chime: 'Chime', none: 'Ready' };

export function SourceIcon({ source }: { source: SourceKind }) {
  if (source === 'airplay') return <IconAirplay />;
  if (source === 'bluetooth') return <IconBluetooth />;
  return <IconRadio />;
}

/** "● DAB ▂▅▇ 72%" / "● AIRPLAY": the live dot answers "is audio flowing", the rest says which mode. */
export function SourceStatus() {
  const s = useState();
  const np = s.now_playing;
  const flowing = s.audio.audio_flowing || s.airplay.playing || s.bluetooth.playing;
  return (
    <>
      <span className={`dot ${flowing ? 'dot-live' : 'dot-warn'}`} />
      <span className="f-mode">{SOURCE_LABEL[np.source]}</span>
      {np.source === 'dab' && np.signal !== null && (
        <><SignalBars percent={np.signal} /><span className="pct">{np.signal}%</span></>
      )}
    </>
  );
}
