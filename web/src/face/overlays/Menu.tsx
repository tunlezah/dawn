import { useEffect, useRef, useState as useReactState } from 'react';
import type { ChangeEvent, PointerEvent } from 'react';
import { useState } from '../../shared/store';
import { actions } from '../../shared/api';
import { IconBack, IconLight, IconMoon, IconMuted, IconNap, IconPlay, IconPower, IconRadio, IconSun, IconVolume } from '../components/icons';

const TITLES: Record<string, string> = { presets: 'Presets', nap: 'Nap timer', sleep: 'Sleep timer', brightness: 'Brightness' };
const stop = (e: PointerEvent) => e.stopPropagation();
const run = (p: Promise<unknown>) => p.catch(() => {});
/** While a finger rests on the sheet, core is told every few seconds so the menu's auto-close never fires mid-gesture. */
const KEEPALIVE_MS = 4000;
/** Volume sets are throttled to this while dragging; the final value is always sent on release. */
const VOLUME_THROTTLE_MS = 80;
/** A press shorter than this is a tap; core shows the shutdown countdown after the same 0.5 s. */
const TAP_MS = 500;

/** Hold the menu open while `held`: one activity ping now, then one every KEEPALIVE_MS. */
function useHold(held: boolean) {
  useEffect(() => {
    if (!held) return;
    run(actions.menuActivity());
    const id = window.setInterval(() => run(actions.menuActivity()), KEEPALIVE_MS);
    return () => { clearInterval(id); run(actions.menuActivity()); };
  }, [held]);
}

/** Volume rail: mute toggle · slider · level. Dragging sets the volume live; the thumb follows the finger,
 *  not the round-trip, and the menu timer is paused for as long as the finger is down. */
function VolumeRail() {
  const s = useState();
  const [drag, setDrag] = useReactState<number | null>(null);
  const [held, setHeld] = useReactState(false);
  useHold(held);
  const lastSent = useRef(0);
  const pending = useRef<number | null>(null);
  const flushTimer = useRef<number | null>(null);
  const settle = useRef<number | null>(null);

  const send = (v: number) => {
    const now = Date.now();
    if (now - lastSent.current >= VOLUME_THROTTLE_MS) { lastSent.current = now; pending.current = null; run(actions.setVolume(v)); return; }
    pending.current = v;
    if (flushTimer.current === null) {
      flushTimer.current = window.setTimeout(() => {
        flushTimer.current = null;
        if (pending.current !== null) { lastSent.current = Date.now(); run(actions.setVolume(pending.current)); pending.current = null; }
      }, VOLUME_THROTTLE_MS);
    }
  };
  const onChange = (e: ChangeEvent<HTMLInputElement>) => { const v = Number(e.target.value); setDrag(v); send(v); };
  const onDown = (e: PointerEvent<HTMLInputElement>) => { stop(e); if (settle.current) { clearTimeout(settle.current); settle.current = null; } setHeld(true); };
  const onUp = () => {
    setHeld(false);
    if (pending.current !== null) { if (flushTimer.current) { clearTimeout(flushTimer.current); flushTimer.current = null; } lastSent.current = Date.now(); run(actions.setVolume(pending.current)); pending.current = null; }
    // keep showing the dragged value until core's state has caught up
    settle.current = window.setTimeout(() => { settle.current = null; setDrag(null); }, 600);
  };
  useEffect(() => () => { if (flushTimer.current) clearTimeout(flushTimer.current); if (settle.current) clearTimeout(settle.current); }, []);

  const v = drag ?? s.audio.volume;
  const muted = s.audio.muted && drag === null;
  return (
    <div className={`f-rail ${muted ? 'muted' : ''}`} onPointerDown={stop}>
      <button aria-label={muted ? 'Unmute' : 'Mute'} onClick={() => run(actions.mute())}>{muted ? <IconMuted /> : <IconVolume level={v === 0 ? 0 : v < 50 ? 1 : 2} />}</button>
      <input id="face-volume" type="range" min={0} max={100} value={v} aria-label="Volume" style={{ ['--v' as string]: `${v}%` }}
        onChange={onChange} onPointerDown={onDown} onPointerUp={onUp} onPointerCancel={onUp} />
      <span className="num tnum">{muted ? '—' : v}</span>
    </div>
  );
}

/** The big button, on screen. Pointer down/up are sent to core as button_down/button_up, so a tap is the
 *  button's short press (playing → standby) and a 3 s hold is the same shutdown countdown as the GPIO button.
 *  From standby a tap plays the first preset (what the encoder push does there). */
function PowerTile({ close }: { close: () => void }) {
  const s = useState();
  const playing = s.audio.active_source !== 'none';
  const downAt = useRef(0);
  // down, up (and the preset) go one after the other: an up that overtook its down would leave core's
  // hold timer running into the shutdown countdown
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const send = (f: () => Promise<unknown>) => { queue.current = run(queue.current.then(f, f)); };
  const onDown = (e: PointerEvent<HTMLButtonElement>) => {
    stop(e);
    e.currentTarget.setPointerCapture(e.pointerId);
    downAt.current = Date.now();
    run(actions.menuActivity());
    send(() => actions.input('button_down'));
  };
  const onUp = () => {
    if (!downAt.current) return;
    const held = Date.now() - downAt.current;
    downAt.current = 0;
    send(() => actions.input('button_up'));
    if (held < TAP_MS) {
      if (!playing) send(() => actions.nextPreset());
      close();
    }
  };
  return (
    <button className={`tile power ${playing ? 'standby' : 'on'}`} aria-label={playing ? 'Standby' : 'Radio on'} onPointerDown={onDown} onPointerUp={onUp} onPointerCancel={onUp}>
      {playing ? <IconPower /> : <IconPlay />}
      {playing ? 'Standby' : 'Radio on'}
      <span className="hint">hold to shut down</span>
    </button>
  );
}

/** Menu sheet: slides up over the player, which stays visible above it. Five tiles and the volume rail;
 *  sub-pages open inside the same sheet. A tap on the player above (or the 15 s timer) closes it. */
export function Menu() {
  const s = useState();
  const page = s.face.menu_page ?? '';
  const close = () => run(actions.faceMenu(false));
  const go = (p: string) => run(actions.faceMenu(true, p));
  const onSheetDown = (e: PointerEvent) => { stop(e); run(actions.menuActivity()); };

  return (
    <>
      <div className="face-scrim fade-in" />
      <div className={`face-menu slide-up ${page ? 'tall' : ''}`} onPointerDown={onSheetDown}>
        <div className="grab" />
        {page && (
          <div className="f-header">
            <div className="f-mode">{TITLES[page] ?? 'Menu'}</div>
            <button className="face-chip" onClick={() => go('')}><IconBack />back</button>
          </div>
        )}
        {!page && (
          <>
            <div className="tiles">
              <button className="tile" onClick={() => go('presets')}><IconRadio />Presets</button>
              <button className="tile" onClick={() => go('nap')}><IconNap />Nap</button>
              <button className="tile" onClick={() => go('sleep')}><IconMoon />Sleep</button>
              <button className="tile" onClick={() => go('brightness')}><IconSun />Brightness</button>
              <PowerTile close={close} />
            </div>
            {s.alarms.light_wake_active && <button className="tile wide" onClick={() => { run(actions.dismissMessage()); close(); }}><IconLight />Dismiss light</button>}
            <VolumeRail />
          </>
        )}
        {page === 'presets' && (
          <div className="page grid grid-cols-3 gap-[3vmin]">
            {s.presets.length === 0 && <div className="f-sub col-span-3">No presets yet. Star a station while it plays, or add them from the Radio page.</div>}
            {s.presets.map((p) => (
              <button key={p.id} className={`tile ${s.now_playing.source !== 'none' && p.source === (s.now_playing.station_sid ? `dab:${s.now_playing.station_sid}` : s.now_playing.url) ? 'active' : ''}`} onClick={() => { run(actions.play(p.source)); close(); }}>
                {p.logo_url ? <img src={p.logo_url} alt="" /> : <IconRadio />}
                <span className="truncate max-w-full text-[3.6vmin]">{p.label}</span>
              </button>
            ))}
          </div>
        )}
        {page === 'nap' && (
          <div className="page grid grid-cols-4 gap-[3vmin]">
            {s.timers.nap_choices.map((m) => <button key={m} className="tile" onClick={() => { run(actions.nap(m)); close(); }}><span className="num tnum">{m}</span><span>min</span></button>)}
            {s.timers.nap && <button className="tile wide col-span-4" onClick={() => { run(actions.cancelNap()); close(); }}>Cancel nap</button>}
          </div>
        )}
        {page === 'sleep' && (
          <div className="page grid grid-cols-5 gap-[3vmin]">
            {s.timers.sleep_choices.map((m) => <button key={m} className="tile" onClick={() => { run(actions.sleep(m)); close(); }}><span className="num tnum">{m}</span><span>min</span></button>)}
            {s.timers.sleep && <button className="tile wide col-span-5" onClick={() => { run(actions.cancelSleep()); close(); }}>Cancel sleep timer</button>}
          </div>
        )}
        {page === 'brightness' && (
          <div className="page flex flex-col gap-[3vmin] justify-center">
            <div className="flex items-center gap-[3vmin]">
              <button className={`face-chip ${s.display.mode === 'auto' ? 'active' : ''}`} onClick={() => run(actions.brightnessMode('auto'))}>Auto</button>
              <button className={`face-chip ${s.display.mode === 'manual' ? 'active' : ''}`} onClick={() => run(actions.brightnessMode('manual'))}>Manual</button>
              <span className="f-meta tnum">{s.display.lux !== null ? `${Math.round(s.display.lux)} lx` : 'no sensor'} · {s.display.brightness}%</span>
            </div>
            <input id="face-brightness" type="range" min={1} max={100} defaultValue={s.display.brightness} className="h-[4vmin]" aria-label="Brightness" onChange={(e) => run(actions.brightness(Number(e.target.value)))} />
          </div>
        )}
      </div>
    </>
  );
}
