import type { PointerEvent } from 'react';
import { useState } from '../../shared/store';
import { actions } from '../../shared/api';
import { IconBack, IconClose, IconLight, IconMoon, IconNap, IconRadio, IconStop, IconSun } from '../components/icons';

const TITLES: Record<string, string> = { presets: 'Presets', nap: 'Nap timer', sleep: 'Sleep timer', brightness: 'Brightness' };

export function Menu() {
  const s = useState();
  const page = s.face.menu_page ?? '';
  const close = () => actions.faceMenu(false).catch(() => {});
  const go = (p: string) => actions.faceMenu(true, p).catch(() => {});
  const stop = (e: PointerEvent) => e.stopPropagation();

  return (
    <div className="face-menu fade-in" onPointerDown={stop}>
      <div className="f-header">
        <div className="f-mode">{TITLES[page] ?? 'Menu'}</div>
        <button className="face-chip" onPointerDown={stop} onClick={page ? () => go('') : close}>{page ? <><IconBack />back</> : <>close<IconClose /></>}</button>
      </div>
      {!page && (
        <div className="grid grid-cols-4 gap-[3vmin] flex-1">
          <button className="tile" onClick={() => go('presets')}><IconRadio />Presets</button>
          <button className="tile" onClick={() => go('nap')}><IconNap />Nap</button>
          <button className="tile" onClick={() => go('sleep')}><IconMoon />Sleep</button>
          <button className="tile" onClick={() => go('brightness')}><IconSun />Brightness</button>
          {s.audio.active_source !== 'none' && <button className="tile wide col-span-2" onClick={() => { actions.standby(); close(); }}><IconStop />Stop playback</button>}
          {s.alarms.light_wake_active && <button className="tile wide col-span-2" onClick={() => { actions.dismissMessage(); close(); }}><IconLight />Dismiss light</button>}
        </div>
      )}
      {page === 'presets' && (
        <div className="grid grid-cols-3 gap-[3vmin] overflow-y-auto">
          {s.presets.length === 0 && <div className="f-sub col-span-3">No presets yet. Star a station while it plays, or add them from the Radio page.</div>}
          {s.presets.map((p) => (
            <button key={p.id} className={`tile ${s.now_playing.source !== 'none' && p.source === (s.now_playing.station_sid ? `dab:${s.now_playing.station_sid}` : s.now_playing.url) ? 'active' : ''}`} onClick={() => { actions.play(p.source); close(); }}>
              {p.logo_url ? <img src={p.logo_url} alt="" /> : <IconRadio />}
              <span className="truncate max-w-full text-[3.6vmin]">{p.label}</span>
            </button>
          ))}
        </div>
      )}
      {page === 'nap' && (
        <div className="grid grid-cols-4 gap-[3vmin]">
          {s.timers.nap_choices.map((m) => <button key={m} className="tile" onClick={() => { actions.nap(m); close(); }}><span className="num tnum">{m}</span><span>min</span></button>)}
          {s.timers.nap && <button className="tile wide col-span-4" onClick={() => { actions.cancelNap(); close(); }}>Cancel nap</button>}
        </div>
      )}
      {page === 'sleep' && (
        <div className="grid grid-cols-5 gap-[3vmin]">
          {s.timers.sleep_choices.map((m) => <button key={m} className="tile" onClick={() => { actions.sleep(m); close(); }}><span className="num tnum">{m}</span><span>min</span></button>)}
          {s.timers.sleep && <button className="tile wide col-span-5" onClick={() => { actions.cancelSleep(); close(); }}>Cancel sleep timer</button>}
        </div>
      )}
      {page === 'brightness' && (
        <div className="flex flex-col gap-[3vmin] flex-1 justify-center">
          <div className="flex items-center gap-[3vmin]">
            <button className={`face-chip ${s.display.mode === 'auto' ? 'active' : ''}`} onClick={() => actions.brightnessMode('auto')}>Auto</button>
            <button className={`face-chip ${s.display.mode === 'manual' ? 'active' : ''}`} onClick={() => actions.brightnessMode('manual')}>Manual</button>
            <span className="f-meta tnum">{s.display.lux !== null ? `${Math.round(s.display.lux)} lx` : 'no sensor'} · {s.display.brightness}%</span>
          </div>
          <input type="range" min={1} max={100} defaultValue={s.display.brightness} className="h-[4vmin]" onChange={(e) => actions.brightness(Number(e.target.value))} />
        </div>
      )}
    </div>
  );
}
