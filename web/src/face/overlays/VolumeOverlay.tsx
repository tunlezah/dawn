import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { IconMuted, IconVolume } from '../components/icons';

export function VolumeOverlay() {
  const s = useState();
  const until = s.audio.volume_overlay_until ? new Date(s.audio.volume_overlay_until).getTime() : 0;
  const [visible, setVisible] = useReactState(false);
  useEffect(() => {
    const left = until - Date.now();
    if (left <= 0) { setVisible(false); return; }
    setVisible(true);
    const id = setTimeout(() => setVisible(false), left);
    return () => clearTimeout(id);
  }, [until, s.audio.volume]);
  if (!visible || s.face.menu_open) return null;
  const v = s.audio.volume;
  return (
    <div className="face-overlay fade-in">
      <div className="f-panel px-[6vmin] py-[3.6vmin] flex flex-col items-center gap-[2vmin]">
        <div className="f-mode flex items-center gap-[2vmin]">{s.audio.muted ? <IconMuted /> : <IconVolume level={v === 0 ? 0 : v < 50 ? 1 : 2} />}{s.audio.muted ? 'Muted' : 'Volume'}</div>
        <div className="face-time f-ambient-clock compact" style={{ fontSize: '16vmin' }}>{s.audio.muted ? '—' : v}</div>
        <div className="vol-bar"><div style={{ width: `${s.audio.muted ? 0 : v}%` }} /></div>
      </div>
    </div>
  );
}
