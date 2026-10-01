import { useEffect } from 'react';
import { useDawn } from '../shared/store';
import { actions } from '../shared/api';
import { Standby } from './states/Standby';
import { Playing } from './states/Playing';
import { Ringing } from './states/Ringing';
import { Countdown } from './states/Countdown';
import { Setup } from './states/Setup';
import { Message } from './states/Message';
import { LightWake } from './states/LightWake';
import { VolumeOverlay } from './overlays/VolumeOverlay';
import { Menu } from './overlays/Menu';
import { ShutdownOverlay } from './overlays/ShutdownOverlay';

export function Face() {
  const { state: s, connected } = useDawn();

  useEffect(() => {
    document.documentElement.dataset.palette = s.display.palette;
    document.documentElement.classList.toggle('low-cpu', s.display.low_cpu);
  }, [s.display.palette, s.display.low_cpu]);

  // Whole-screen touch: snooze while ringing, otherwise tell core (it opens the menu / wakes the face).
  const onPointerDown = () => {
    if (s.face.mode === 'ringing') actions.snooze().catch(() => {});
    else if (s.face.mode === 'message' || s.face.mode === 'lightwake') actions.dismissMessage().catch(() => {});
    else if (!s.face.menu_open) actions.touch().catch(() => {});
  };

  let screen;
  switch (s.face.mode) {
    case 'ringing': screen = <Ringing />; break;
    case 'playing': screen = <Playing />; break;
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
      {!connected && <div className="absolute top-[2vmin] right-[3vmin] face-dots"><span className="dot dot-err" />offline</div>}
      {s.display.overlay_dim > 0 && <div className="face-dim" style={{ opacity: Math.min(0.92, s.display.overlay_dim) }} />}
      </div>
    </div>
  );
}
