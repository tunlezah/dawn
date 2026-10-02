import { useEffect, useRef, useState as useReactState } from 'react';
import { useDawn } from '../shared/store';
import { actions } from '../shared/api';
import { Standby } from './states/Standby';
import { Playing } from './states/Playing';
import { Ringing } from './states/Ringing';
import { Countdown } from './states/Countdown';
import { Setup } from './states/Setup';
import { Message } from './states/Message';
import { LightWake } from './states/LightWake';
import { Ambient } from './components/Ambient';
import { VolumeOverlay } from './overlays/VolumeOverlay';
import { Menu } from './overlays/Menu';
import { ShutdownOverlay } from './overlays/ShutdownOverlay';

export function Face() {
  const { state: s, connected } = useDawn();

  useEffect(() => {
    document.documentElement.dataset.palette = s.display.palette;
    document.documentElement.classList.toggle('low-cpu', s.display.low_cpu);
  }, [s.display.palette, s.display.low_cpu]);

  // Idle while playing -> ambient clock with a now-playing strip. Any touch, or a new track, brings the player back.
  const [ambient, setAmbient] = useReactState(false);
  const lastTouch = useRef(Date.now());
  const np = s.now_playing;
  const trackKey = `${np.source}|${np.title}|${np.artist}|${np.station}`;
  useEffect(() => { lastTouch.current = Date.now(); setAmbient(false); }, [trackKey, s.face.mode, s.face.menu_open]);
  const after = s.display.ambient_after_s;
  useEffect(() => {
    if (s.face.mode !== 'playing' || after <= 0 || s.face.menu_open) { setAmbient(false); return; }
    const id = window.setInterval(() => { if (Date.now() - lastTouch.current >= after * 1000) setAmbient(true); }, 500);
    return () => clearInterval(id);
  }, [s.face.mode, after, s.face.menu_open]);

  // Whole-screen touch: snooze while ringing, leave ambient, otherwise tell core (it opens the menu / wakes the face).
  const onPointerDown = () => {
    lastTouch.current = Date.now();
    if (s.face.mode === 'ringing') actions.snooze().catch(() => {});
    else if (ambient) setAmbient(false);
    else if (s.face.mode === 'message' || s.face.mode === 'lightwake') actions.dismissMessage().catch(() => {});
    else if (!s.face.menu_open) actions.touch().catch(() => {});
  };

  let screen;
  switch (s.face.mode) {
    case 'ringing': screen = <Ringing />; break;
    case 'playing': screen = ambient ? <Ambient compact /> : <Playing />; break;
    case 'countdown': screen = <Countdown />; break;
    case 'setup': screen = <Setup />; break;
    case 'message': screen = <Message />; break;
    case 'lightwake': screen = <LightWake />; break;
    default: screen = <Standby />;
  }

  return (
    <div className={`face-root ${s.display.layout === 'round' ? 'round' : ''}`} onPointerDown={onPointerDown}>
      <div className="face-canvas">
        {screen}
        {s.face.menu_open && s.face.mode !== 'ringing' && <Menu />}
        <VolumeOverlay />
        {s.face.shutdown_countdown !== null && <ShutdownOverlay seconds={s.face.shutdown_countdown} />}
        {!connected && <div className="absolute top-[2vmin] right-[3vmin] face-dots z-40"><span className="dot dot-err" />offline</div>}
        {s.display.overlay_dim > 0 && <div className="face-dim" style={{ opacity: Math.min(0.92, s.display.overlay_dim) }} />}
      </div>
    </div>
  );
}
