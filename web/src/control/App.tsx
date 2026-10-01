import { useEffect, useState } from 'react';
import { useDawn } from '../shared/store';
import { navigate, usePath } from '../shared/router';
import { api } from '../shared/api';
import { Home } from './pages/Home';
import { Alarms } from './pages/Alarms';
import { Radio } from './pages/Radio';
import { Timers } from './pages/Timers';
import { Display } from './pages/Display';
import { Audio } from './pages/Audio';
import { Status } from './pages/Status';
import { Settings } from './pages/Settings';
import { About } from './pages/About';

const NAV: { path: string; label: string; icon: string }[] = [
  { path: '/', label: 'Home', icon: '⌂' },
  { path: '/alarms', label: 'Alarms', icon: '⏰' },
  { path: '/radio', label: 'Radio', icon: '📻' },
  { path: '/timers', label: 'Timers', icon: '⏳' },
  { path: '/display', label: 'Display', icon: '☀' },
  { path: '/audio', label: 'Audio', icon: '🔊' },
  { path: '/status', label: 'Status', icon: '◉' },
  { path: '/settings', label: 'Settings', icon: '⚙' },
  { path: '/about', label: 'About', icon: 'ⓘ' },
];

function Page({ path }: { path: string }) {
  switch (path) {
    case '/': return <Home />;
    case '/alarms': return <Alarms />;
    case '/radio': return <Radio />;
    case '/timers': return <Timers />;
    case '/display': return <Display />;
    case '/audio': return <Audio />;
    case '/status': return <Status />;
    case '/settings': return <Settings />;
    case '/about': return <About />;
    default: return <Home />;
  }
}

function PinGate({ onOk }: { onOk: () => void }) {
  const [pin, setPin] = useState('');
  const [err, setErr] = useState(false);
  return (
    <div className="min-h-full flex items-center justify-center p-6">
      <form className="card p-6 w-full max-w-xs space-y-3" onSubmit={async (e) => { e.preventDefault(); try { await api.post('/api/auth/login', { pin }); onOk(); } catch { setErr(true); } }}>
        <h1 className="text-xl font-semibold">Dawn</h1>
        <p className="text-sm text-muted">Enter the PIN to control this clock.</p>
        <input type="password" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} placeholder="PIN" autoFocus />
        {err && <p className="text-err text-sm">Wrong PIN</p>}
        <button className="btn btn-primary w-full">Unlock</button>
      </form>
    </div>
  );
}

export function App() {
  const { state, connected } = useDawn();
  const path = usePath();
  const [locked, setLocked] = useState<boolean | null>(null);

  useEffect(() => {
    api.get<{ required: boolean; authenticated: boolean }>('/api/auth/status').then((s) => setLocked(s.required && !s.authenticated)).catch(() => setLocked(false));
    const on = () => setLocked(true);
    window.addEventListener('dawn:unauthorized', on);
    return () => window.removeEventListener('dawn:unauthorized', on);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.palette = state.settings.theme === 'light' ? 'light' : 'dark';
  }, [state.settings.theme]);

  if (locked) return <PinGate onOk={() => setLocked(false)} />;

  return (
    <div className="min-h-full flex flex-col sm:flex-row">
      <aside className="hidden sm:flex flex-col w-56 shrink-0 border-r border-border p-4 gap-1 sticky top-0 h-screen">
        <div className="flex items-center gap-2 px-2 mb-4">
          <img src="/favicon.svg" alt="" className="w-7 h-7" />
          <span className="font-semibold text-lg">{state.settings.name || 'Dawn'}</span>
        </div>
        {NAV.map((n) => (
          <button key={n.path} onClick={() => navigate(n.path)} className={`text-left px-3 py-2 rounded-xl flex items-center gap-3 ${path === n.path ? 'bg-elev-2 text-fg' : 'text-muted hover:text-fg'}`}>
            <span className="w-5 text-center">{n.icon}</span>{n.label}
          </button>
        ))}
        <div className="mt-auto text-xs text-faint px-2 flex items-center gap-2">
          <span className={`dot ${connected ? 'dot-ok' : 'dot-err'}`} /> {connected ? 'connected' : 'reconnecting…'}
        </div>
      </aside>
      <main className="flex-1 min-w-0 p-4 pb-24 sm:pb-6 max-w-3xl mx-auto w-full">
        {!connected && <div className="mb-3 text-sm text-warn chip">Reconnecting to {state.system.hostname || 'dawn'}…</div>}
        {state.system.config_error && <div className="mb-3 text-sm text-err chip">Config error: {state.system.config_error}</div>}
        <Page path={path} />
      </main>
      <nav className="sm:hidden fixed bottom-0 inset-x-0 border-t border-border bg-elev/95 backdrop-blur flex justify-around px-1 py-1.5 pb-[max(env(safe-area-inset-bottom),6px)] overflow-x-auto">
        {NAV.slice(0, 5).map((n) => (
          <button key={n.path} onClick={() => navigate(n.path)} className={`flex flex-col items-center text-[11px] px-2 py-1 rounded-lg ${path === n.path ? 'text-accent' : 'text-muted'}`}>
            <span className="text-lg leading-none">{n.icon}</span>{n.label}
          </button>
        ))}
        <button onClick={() => navigate(['/audio', '/status', '/settings', '/about'].includes(path) ? path : '/settings')} className={`flex flex-col items-center text-[11px] px-2 py-1 rounded-lg ${['/audio', '/status', '/settings', '/about'].includes(path) ? 'text-accent' : 'text-muted'}`}>
          <span className="text-lg leading-none">⋯</span>More
        </button>
      </nav>
    </div>
  );
}
