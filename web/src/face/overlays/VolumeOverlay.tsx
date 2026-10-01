import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';

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
  if (!visible) return null;
  return (
    <div className="face-overlay fade-in">
      <div className="card px-[5vmin] py-[3vmin] flex flex-col items-center gap-[2vmin] bg-elev/95">
        <div className="text-[4vmin] text-muted">{s.audio.muted ? 'Muted' : 'Volume'}</div>
        <div className="text-[10vmin] font-semibold tnum leading-none">{s.audio.muted ? '—' : s.audio.volume}</div>
        <div className="vol-bar"><div style={{ width: `${s.audio.muted ? 0 : s.audio.volume}%` }} /></div>
      </div>
    </div>
  );
}
