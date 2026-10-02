import { useEffect, useMemo, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Row, Slider, Switch } from '../../shared/components';
import { actions, api } from '../../shared/api';
import { fmtShort } from '../../shared/time';

interface Pt { lux: number; brightness: number }
interface DisplayCfg {
  layout: 'rect' | 'round'; theme: 'dark' | 'light'; low_cpu: string; show_seconds: boolean; clock_24h: boolean; ambient_after_s: number;
  brightness: { curve: Pt[]; hysteresis_percent: number; slew_s: number; night_lux_threshold: number; night_hysteresis_lux: number; post_sunset_cap_percent: number; post_sunset_cap_minutes: number; sunset_night_palette: boolean; manual_override_until: string; manual_percent: number };
}

function CurvePreview({ curve, lux }: { curve: Pt[]; lux: number | null }) {
  const W = 320, H = 120, PAD = 8;
  const xs = (l: number) => PAD + (Math.log10(l + 1) / Math.log10(10001)) * (W - 2 * PAD);
  const ys = (b: number) => H - PAD - (b / 100) * (H - 2 * PAD);
  const pts = [...curve].sort((a, b) => a.lux - b.lux);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${xs(p.lux).toFixed(1)},${ys(p.brightness).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-32">
      {[1, 10, 100, 1000, 10000].map((l) => <line key={l} x1={xs(l)} x2={xs(l)} y1={PAD} y2={H - PAD} stroke="var(--border)" />)}
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2.5} strokeLinejoin="round" />
      {pts.map((p, i) => <circle key={i} cx={xs(p.lux)} cy={ys(p.brightness)} r={4} fill="var(--accent)" />)}
      {lux !== null && <line x1={xs(lux)} x2={xs(lux)} y1={PAD} y2={H - PAD} stroke="var(--ok)" strokeDasharray="3 3" />}
      {[1, 10, 100, 1000, 10000].map((l) => <text key={l} x={xs(l)} y={H - 1} fontSize={8} fill="var(--fg-faint)" textAnchor="middle">{l >= 1000 ? `${l / 1000}k` : l}</text>)}
    </svg>
  );
}

export function Display() {
  const s = useState();
  const [cfg, setCfg] = useReactState<DisplayCfg | null>(null);
  const [curve, setCurve] = useReactState<Pt[]>([]);
  const [manual, setManual] = useReactState(s.display.brightness);
  useEffect(() => { api.get<{ display: DisplayCfg }>('/api/config').then((c) => { setCfg(c.display); setCurve(c.display.brightness.curve); }); }, [s.version % 50 === 0 ? s.version : 0]);
  useEffect(() => { if (s.display.mode === 'manual') setManual(s.display.target); }, [s.display.target, s.display.mode]);
  const dirty = useMemo(() => cfg && JSON.stringify(curve) !== JSON.stringify(cfg.brightness.curve), [curve, cfg]);
  const patch = (p: Record<string, unknown>) => api.patch('/api/config', { display: p }).then(() => api.get<{ display: DisplayCfg }>('/api/config').then((c) => { setCfg(c.display); setCurve(c.display.brightness.curve); }));
  const d = s.display;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Display</h1>

      <Card title="Brightness" action={<span className="chip">{d.brightness}% · {d.lux !== null ? `${Math.round(d.lux)} lx` : 'no sensor'}</span>}>
        <div className="flex gap-2 mb-3">
          <button className={`btn btn-sm ${d.mode === 'auto' ? 'btn-primary' : ''}`} onClick={() => actions.brightnessMode('auto')}>Auto</button>
          <button className={`btn btn-sm ${d.mode === 'manual' ? 'btn-primary' : ''}`} onClick={() => actions.brightnessMode('manual')}>Manual</button>
          {d.mode === 'manual' && d.manual_until && <span className="text-xs text-muted self-center">until sunrise ({fmtShort(d.manual_until, s.tz, s.settings.clock_24h)})</span>}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted w-14">Manual</span>
          <Slider label="Manual brightness" min={1} max={100} value={manual} onChange={setManual} onCommit={(v) => actions.brightness(v)} />
          <span className="tnum w-10 text-right text-sm">{manual}%</span>
        </div>
        <p className="text-xs text-muted mt-2">Moving the slider switches to manual until the next sunrise (configurable). Driver: {d.backlight_driver}{!d.sensor_found && ' · no light sensor: using the sunrise/sunset schedule'}</p>
      </Card>

      <Card title="Auto curve" action={dirty ? <button className="btn btn-sm btn-primary" onClick={() => api.put('/api/display/curve', { curve }).then(() => patch({}))}>Save curve</button> : undefined}>
        <CurvePreview curve={curve} lux={d.lux} />
        <div className="space-y-1 mt-2">
          {curve.map((p, i) => (
            <div key={i} className="flex items-center gap-2 text-sm">
              <input type="number" min={0} step="any" value={p.lux} onChange={(e) => setCurve(curve.map((q, j) => (j === i ? { ...q, lux: Number(e.target.value) } : q)))} className="w-24" aria-label="lux" />
              <span className="text-muted">lx →</span>
              <input type="range" min={1} max={100} value={p.brightness} onChange={(e) => setCurve(curve.map((q, j) => (j === i ? { ...q, brightness: Number(e.target.value) } : q)))} aria-label="brightness" />
              <span className="tnum w-10 text-right">{p.brightness}%</span>
              <button className="btn btn-ghost btn-sm" onClick={() => setCurve(curve.filter((_, j) => j !== i))} disabled={curve.length <= 2} aria-label="Remove point">✕</button>
            </div>
          ))}
          <button className="btn btn-sm" onClick={() => setCurve([...curve, { lux: (curve[curve.length - 1]?.lux ?? 0) * 2 + 10, brightness: Math.min(100, (curve[curve.length - 1]?.brightness ?? 50) + 5) }])}>+ point</button>
        </div>
        {cfg && (
          <div className="grid grid-cols-2 gap-3 mt-4 text-sm">
            <Row label="Hysteresis %" hint="ignore smaller target changes"><input type="number" min={0} max={30} value={cfg.brightness.hysteresis_percent} onChange={(e) => patch({ brightness: { hysteresis_percent: Number(e.target.value) } })} className="w-20" /></Row>
            <Row label="Slew (s)"><input type="number" min={0} max={20} step={0.5} value={cfg.brightness.slew_s} onChange={(e) => patch({ brightness: { slew_s: Number(e.target.value) } })} className="w-20" /></Row>
          </div>
        )}
      </Card>

      {cfg && (
        <Card title="Night palette" action={<span className={`chip ${d.night ? 'text-accent' : ''}`}>{d.night ? 'night now' : 'day'}</span>}>
          <Row label="Below lux" hint={`exit above ${cfg.brightness.night_lux_threshold + cfg.brightness.night_hysteresis_lux} lx`}><input type="number" min={0} step="any" value={cfg.brightness.night_lux_threshold} onChange={(e) => patch({ brightness: { night_lux_threshold: Number(e.target.value) } })} className="w-20" /></Row>
          <Row label="Night palette from sunset to sunrise" hint={`schedule: ${fmtShort(d.sunrise, s.tz, s.settings.clock_24h)} – ${fmtShort(d.sunset, s.tz, s.settings.clock_24h)} · used when there is no sensor`}><Switch on={cfg.brightness.sunset_night_palette} onChange={(v) => patch({ brightness: { sunset_night_palette: v } })} /></Row>
          <Row label="Cap after sunset" hint={`${cfg.brightness.post_sunset_cap_minutes} min after sunset`}><input type="number" min={1} max={100} value={cfg.brightness.post_sunset_cap_percent} onChange={(e) => patch({ brightness: { post_sunset_cap_percent: Number(e.target.value) } })} className="w-20" /><span className="text-sm text-muted">%</span></Row>
          <Row label="Manual override lasts"><select value={cfg.brightness.manual_override_until} onChange={(e) => patch({ brightness: { manual_override_until: e.target.value } })} className="w-40"><option value="sunrise">until sunrise</option><option value="forever">until auto is re-enabled</option></select></Row>
        </Card>
      )}

      {cfg && (
        <Card title="Face">
          <Row label="Layout"><select value={cfg.layout} onChange={(e) => patch({ layout: e.target.value })} className="w-36"><option value="rect">Rectangular</option><option value="round">Round display</option></select></Row>
          <Row label="Theme (day)"><select value={cfg.theme} onChange={(e) => patch({ theme: e.target.value })} className="w-36"><option value="dark">Dark</option><option value="light">Light</option></select></Row>
          <Row label="24-hour clock"><Switch on={cfg.clock_24h} onChange={(v) => patch({ clock_24h: v })} /></Row>
          <Row label="Show seconds"><Switch on={cfg.show_seconds} onChange={(v) => patch({ show_seconds: v })} /></Row>
          <Row label="Ambient clock while playing" hint="seconds without a touch before the player gives way to the clock · 0 = never"><input type="number" min={0} max={600} value={cfg.ambient_after_s} onChange={(e) => patch({ ambient_after_s: Number(e.target.value) })} className="w-24" /></Row>
          <Row label="Low-CPU animations" hint="auto = on for Zero 2 W / 3B+"><select value={cfg.low_cpu} onChange={(e) => patch({ low_cpu: e.target.value })} className="w-28"><option value="auto">auto</option><option value="on">on</option><option value="off">off</option></select></Row>
        </Card>
      )}
    </div>
  );
}
