import { useState } from '../../shared/store';
import { actions } from '../../shared/api';

export function Menu() {
  const s = useState();
  const page = s.face.menu_page;
  const close = () => actions.faceMenu(false).catch(() => {});
  const go = (p: string) => actions.faceMenu(true, p).catch(() => {});
  const stop = (e: React.PointerEvent) => e.stopPropagation();

  return (
    <div className="face-menu fade-in" onPointerDown={stop}>
      <div className="flex items-center justify-between">
        <div className="text-[4.5vmin] font-semibold">{page === 'presets' ? 'Presets' : page === 'nap' ? 'Nap timer' : page === 'sleep' ? 'Sleep timer' : page === 'brightness' ? 'Brightness' : 'Menu'}</div>
        <button className="face-chip" onPointerDown={stop} onClick={page ? () => go('') : close}>{page ? '‹ back' : 'close ✕'}</button>
      </div>
      {!page && (
        <div className="grid grid-cols-4 gap-[3vmin] flex-1">
          <button className="tile" onClick={() => go('presets')}><span className="text-[7vmin]">📻</span>Presets</button>
          <button className="tile" onClick={() => go('nap')}><span className="text-[7vmin]">😴</span>Nap</button>
          <button className="tile" onClick={() => go('sleep')}><span className="text-[7vmin]">🌙</span>Sleep</button>
          <button className="tile" onClick={() => go('brightness')}><span className="text-[7vmin]">☀</span>Brightness</button>
          {s.audio.active_source !== 'none' && <button className="tile col-span-2" onClick={() => { actions.standby(); close(); }}>⏹ Stop playback</button>}
          {s.alarms.light_wake_active && <button className="tile col-span-2" onClick={() => { actions.dismissMessage(); close(); }}>Dismiss light</button>}
        </div>
      )}
      {page === 'presets' && (
        <div className="grid grid-cols-3 gap-[3vmin] overflow-y-auto">
          {s.presets.length === 0 && <div className="text-muted text-[4vmin] col-span-3">No presets yet. Add them from the Radio page.</div>}
          {s.presets.map((p) => (
            <button key={p.id} className="tile" onClick={() => { actions.play(p.source); close(); }}>
              {p.logo_url ? <img src={p.logo_url} alt="" className="w-[9vmin] h-[9vmin] rounded-[1.5vmin] object-cover" /> : <span className="text-[7vmin]">📻</span>}
              <span className="truncate max-w-full text-[3.6vmin]">{p.label}</span>
            </button>
          ))}
        </div>
      )}
      {page === 'nap' && (
        <div className="grid grid-cols-4 gap-[3vmin]">
          {s.timers.nap_choices.map((m) => <button key={m} className="tile" onClick={() => { actions.nap(m); close(); }}><span className="text-[7vmin] tnum">{m}</span>min</button>)}
          {s.timers.nap && <button className="tile col-span-4" onClick={() => { actions.cancelNap(); close(); }}>Cancel nap</button>}
        </div>
      )}
      {page === 'sleep' && (
        <div className="grid grid-cols-5 gap-[3vmin]">
          {s.timers.sleep_choices.map((m) => <button key={m} className="tile" onClick={() => { actions.sleep(m); close(); }}><span className="text-[7vmin] tnum">{m}</span>min</button>)}
          {s.timers.sleep && <button className="tile col-span-5" onClick={() => { actions.cancelSleep(); close(); }}>Cancel sleep timer</button>}
        </div>
      )}
      {page === 'brightness' && (
        <div className="flex flex-col gap-[3vmin] flex-1 justify-center">
          <div className="flex items-center gap-[3vmin]">
            <button className={`face-chip ${s.display.mode === 'auto' ? 'text-accent' : ''}`} onClick={() => actions.brightnessMode('auto')}>Auto</button>
            <button className={`face-chip ${s.display.mode === 'manual' ? 'text-accent' : ''}`} onClick={() => actions.brightnessMode('manual')}>Manual</button>
            <span className="text-muted text-[3.6vmin]">{s.display.lux !== null ? `${Math.round(s.display.lux)} lx` : 'no sensor'} · {s.display.brightness}%</span>
          </div>
          <input type="range" min={1} max={100} defaultValue={s.display.brightness} className="h-[4vmin]" onChange={(e) => actions.brightness(Number(e.target.value))} />
        </div>
      )}
    </div>
  );
}
