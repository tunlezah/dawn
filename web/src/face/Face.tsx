import { useEffect, useRef, useState as useReactState } from 'react';
import { useDawn } from '../shared/store';
import { actions } from '../shared/api';
import { Standby } from './states/Standby';
import { Sleep } from './states/Sleep';
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
import { useNow } from '../shared/time';
import { orbitAt, secondsOfDay } from './burnin';
import { useBackup } from './backup';

export function Face() {
  const { state: s, connected } = useDawn();
  // the face's own safety net: beeps along when core asks, rings by itself when core is down at an alarm
  const backup = useBackup(s, connected);

  // sleep mode is always amber on black, whatever the room's light
  const palette = s.face.mode === 'sleep' ? 'night' : s.display.palette;
  useEffect(() => {
    document.documentElement.dataset.palette = palette;
    document.documentElement.classList.toggle('low-cpu', s.display.low_cpu);
  }, [palette, s.display.low_cpu]);

  // pixel orbit (burn-in): the foreground drifts a pixel a minute along a closed path; backgrounds stay put
  const minute = Math.floor(secondsOfDay(useNow(60_000), s.tz) / 60);
  const orbit = s.display.orbit ? orbitAt(minute) : { x: 0, y: 0 };

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

  // Whole-screen touch: ringing handles its own tap/hold, leave ambient, close the open sheet,
  // otherwise tell core (it opens the menu / wakes the face).
  const onPointerDown = () => {
    lastTouch.current = Date.now();
    if (s.face.mode === 'ringing' || backup.local) return;
    if (ambient) setAmbient(false);
    else if (s.face.mode === 'message' || s.face.mode === 'lightwake') actions.dismissMessage().catch(() => {});
    else if (s.face.menu_open) actions.faceMenu(false).catch(() => {});
    else actions.touch().catch(() => {});
  };

  let screen;
  if (backup.local) screen = <Ringing local={{ label: backup.local.label, snoozedUntil: backup.local.snoozedUntil, why: backup.local.why, onSnooze: backup.snooze, onStop: backup.stop }} />;
  else switch (s.face.mode) {
    case 'ringing': screen = <Ringing />; break;
    case 'playing': screen = ambient ? <Ambient compact /> : <Playing />; break;
    case 'countdown': screen = <Countdown />; break;
    case 'setup': screen = <Setup />; break;
    case 'message': screen = <Message />; break;
    case 'lightwake': screen = <LightWake />; break;
    case 'sleep': screen = <Sleep />; break;
    default: screen = <Standby />;
  }

  return (
    <div className={`face-root ${s.display.layout === 'round' ? 'round' : ''}`} onPointerDown={onPointerDown} data-beep={backup.beeping ? '1' : undefined}>
      <div className="face-canvas" style={{ ['--ox' as string]: `${orbit.x}px`, ['--oy' as string]: `${orbit.y}px` }}>
        {screen}
        {s.face.menu_open && s.face.mode !== 'ringing' && !backup.local && <Menu />}
        <VolumeOverlay />
        {s.face.shutdown_countdown !== null && <ShutdownOverlay seconds={s.face.shutdown_countdown} />}
        {!connected && <div className="absolute top-[2vmin] right-[3vmin] face-dots z-40"><span className="dot dot-err" />offline</div>}
        {s.display.overlay_dim > 0 && !backup.local && <div className="face-dim" style={{ opacity: Math.min(0.92, s.display.overlay_dim) }} />}
      </div>
    </div>
  );
}
