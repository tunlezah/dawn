import { useEffect, useMemo, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Row, Slider, Switch } from '../../shared/components';
import { actions, api, ApiError } from '../../shared/api';
import { fmtDayTime, fmtShort } from '../../shared/time';

interface Pt { lux: number; brightness: number }
interface SleepCfg {
  enabled: boolean; start_at_time: boolean; start: string; start_when_dark: boolean; end_at_time: boolean; end: string; end_before_alarm: boolean;
  alarm_lead_minutes: number; end_when_bright: boolean; dark_lux: number; dark_after_s: number; bright_lux: number; bright_after_s: number;
  jump_every_s: number; level_percent: number; backlight_percent: number | null; screen_off: boolean; wake_percent: number;
}
interface BurnInCfg { pixel_orbit: boolean; strip_autohide_s: number; scene_daily: boolean }
interface DisplayCfg {
  layout: 'rect' | 'round'; theme: 'dark' | 'light'; low_cpu: string; show_seconds: boolean; clock_24h: boolean; ambient_after_s: number; scene: boolean;
  sleep: SleepCfg; burn_in: BurnInCfg;
  brightness: { curve: Pt[]; hysteresis_percent: number; slew_s: number; night_lux_threshold: number; night_hysteresis_lux: number; post_sunset_cap_percent: number; post_sunset_cap_minutes: number; sunset_night_palette: boolean; manual_override_until: string; manual_percent: number };
}

/** A number committed on blur or Enter, never per keystroke. Out of range, not whole when `step` is whole,
 *  or not a number (the browser reports that as an empty value with badInput): put back what is saved. */
function NumField({ value, min, max, step = 1, onCommit, label, className = '!w-20' }: { value: number; min: number; max: number; step?: number; onCommit: (v: number) => void; label: string; className?: string }) {
  const [v, setV] = useReactState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = (el: HTMLInputElement) => {
    const n = Number(v);
    const whole = Number.isInteger(step) ? Number.isInteger(n) : true;
    if (el.validity.badInput || v.trim() === '' || !Number.isFinite(n) || !whole || n < min || n > max) { setV(String(value)); return; }
    if (n !== value) onCommit(n);
  };
  return <input type="number" aria-label={label} min={min} max={max} step={step} value={v} className={className}
    onChange={(e) => setV(e.target.value)} onBlur={(e) => commit(e.currentTarget)} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />;
}

/** Like NumField, but blank means "not set" (null). */
function OptionalNumField({ value, min, max, onCommit, label, placeholder }: { value: number | null; min: number; max: number; onCommit: (v: number | null) => void; label: string; placeholder: string }) {
  const [v, setV] = useReactState(value === null ? '' : String(value));
  useEffect(() => setV(value === null ? '' : String(value)), [value]);
  const commit = (el: HTMLInputElement) => {
    const back = () => setV(value === null ? '' : String(value));
    if (el.validity.badInput) return back();
    const n = v.trim() === '' ? null : Number(v);
    if (n !== null && (!Number.isInteger(n) || n < min || n > max)) return back();
    if (n !== value) onCommit(n);
  };
  return <input type="number" aria-label={label} min={min} max={max} step={1} placeholder={placeholder} value={v} className="!w-20"
    onChange={(e) => setV(e.target.value)} onBlur={(e) => commit(e.currentTarget)} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />;
}

/** HH:MM committed when the field is left: typing "1", "1" must not save 13:30 on the way to 11:30. */
function TimeField({ value, onCommit, label, disabled }: { value: string; onCommit: (v: string) => void; label: string; disabled?: boolean }) {
  const [v, setV] = useReactState(value);
  useEffect(() => setV(value), [value]);
  const commit = () => {
    if (!/^\d{2}:\d{2}$/.test(v)) return setV(value);
    if (v !== value) onCommit(v);
  };
  return <input type="time" aria-label={label} value={v} disabled={disabled} className="!w-36"
    onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }} />;
}

function CommitSlider({ value, min, max, onCommit, label }: { value: number; min: number; max: number; onCommit: (v: number) => void; label: string }) {
  const [v, setV] = useReactState(value);
  useEffect(() => setV(value), [value]);
  return (
    <>
      <div className="w-36"><Slider label={label} min={min} max={max} value={v} onChange={setV} onCommit={(n) => n !== value && onCommit(n)} /></div>
      <span className="tnum w-10 text-right text-sm">{v}%</span>
    </>
  );
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
  const [sleepErr, setSleepErr] = useReactState<string | null>(null);
  const [burnErr, setBurnErr] = useReactState<string | null>(null);
  const patchBurn = (p: Record<string, unknown>) => patch({ burn_in: p }).then(() => setBurnErr(null)).catch((e: unknown) => setBurnErr(e instanceof ApiError ? JSON.stringify(e.detail ?? e.message) : String(e)));
  const patchSleep = (p: Partial<SleepCfg>) => patch({ sleep: p }).then(() => setSleepErr(null)).catch((e: unknown) => setSleepErr(e instanceof ApiError ? JSON.stringify(e.detail ?? e.message) : String(e)));
  const sleepNow = (on: boolean) => api.post('/api/display/sleep', { on }).then(() => setSleepErr(null)).catch((e: unknown) => setSleepErr(e instanceof ApiError ? e.message : String(e)));
  const when = (iso: string | null) => (iso ? fmtDayTime(iso, s.tz, s.settings.clock_24h) : '–');

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
          <Row label="Background image (Standby and ambient clock)" hint="sky, weather and season behind the clock · never in sleep mode or the night palette"><Switch label="Background image" on={cfg.scene} onChange={(v) => patch({ scene: v })} /></Row>
          <Row label="Ambient clock while playing" hint="seconds without a touch before the player gives way to the clock · 0 = never"><input type="number" min={0} max={600} value={cfg.ambient_after_s} onChange={(e) => patch({ ambient_after_s: Number(e.target.value) })} className="w-24" /></Row>
          <Row label="Low-CPU animations" hint="auto = on for Zero 2 W / 3B+"><select value={cfg.low_cpu} onChange={(e) => patch({ low_cpu: e.target.value })} className="w-28"><option value="auto">auto</option><option value="on">on</option><option value="off">off</option></select></Row>
        </Card>
      )}

      {cfg && (
        <Card title="Sleep mode" action={<span className={`chip ${d.sleep ? 'text-accent' : ''}`} data-testid="sleep-status">{!cfg.sleep.enabled ? 'off' : d.sleep ? `asleep · ${d.sleep_reason ?? ''}` : 'awake'}</span>}>
          <p className="text-sm text-muted mb-2">The clock alone, small and amber on black at the lowest backlight, moving every couple of minutes. It replaces Standby only: playing audio stays on screen until it stops, and an alarm always wakes it. A tap shows the full face, dimmed, for a few seconds.</p>
          <Row label="Use sleep mode">
            <Switch label="Use sleep mode" on={cfg.sleep.enabled} onChange={(v) => patchSleep({ enabled: v })} />
          </Row>
          {cfg.sleep.enabled && (d.sleep_next_start || d.sleep_next_end) && (
            <p className="text-xs text-muted pt-1" data-testid="sleep-next">Next sleep {when(d.sleep_next_start)} · next wake {when(d.sleep_next_end)}{!d.sleep_next_start && cfg.sleep.start_when_dark ? ' (or when the room goes dark)' : ''}</p>
          )}
          {cfg.sleep.enabled && (
            <>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted mt-3">Go to sleep (either)</div>
              <Row label="At bedtime" hint="every night, even with the lights on">
                <TimeField label="Bedtime" value={cfg.sleep.start} onCommit={(v) => patchSleep({ start: v })} disabled={!cfg.sleep.start_at_time} />
                <Switch label="At bedtime" on={cfg.sleep.start_at_time} onChange={(v) => patchSleep({ start_at_time: v })} />
              </Row>
              <Row label="When the room goes dark" hint={d.sensor_found ? `below ${cfg.sleep.dark_lux} lx for ${cfg.sleep.dark_after_s} s · now ${d.lux !== null ? `${Math.round(d.lux * 10) / 10} lx` : '–'}${d.room ? ` (${d.room})` : ''}` : 'needs the light sensor (not found)'}>
                <Switch label="When the room goes dark" on={cfg.sleep.start_when_dark} onChange={(v) => patchSleep({ start_when_dark: v })} />
              </Row>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted mt-3">Wake up (whichever comes first)</div>
              <Row label="At the morning time">
                <TimeField label="Morning time" value={cfg.sleep.end} onCommit={(v) => patchSleep({ end: v })} disabled={!cfg.sleep.end_at_time} />
                <Switch label="At the morning time" on={cfg.sleep.end_at_time} onChange={(v) => patchSleep({ end_at_time: v })} />
              </Row>
              <Row label="Before the next alarm" hint="minutes before it (or before its light wake)">
                <NumField label="Minutes before the alarm" value={cfg.sleep.alarm_lead_minutes} min={0} max={120} onCommit={(v) => patchSleep({ alarm_lead_minutes: v })} />
                <Switch label="Before the next alarm" on={cfg.sleep.end_before_alarm} onChange={(v) => patchSleep({ end_before_alarm: v })} />
              </Row>
              <Row label="When the room gets bright" hint={d.sensor_found ? `above ${cfg.sleep.bright_lux} lx for ${cfg.sleep.bright_after_s} s` : 'needs the light sensor (not found)'}>
                <Switch label="When the room gets bright" on={cfg.sleep.end_when_bright} onChange={(v) => patchSleep({ end_when_bright: v })} />
              </Row>
              <details className="mt-2">
                <summary className="text-sm text-muted cursor-pointer select-none py-1">Light levels</summary>
                <Row label="Dark below (lx)"><NumField label="Dark below lux" value={cfg.sleep.dark_lux} min={0} max={1000} step={0.5} onCommit={(v) => patchSleep({ dark_lux: v })} /></Row>
                <Row label="… for (s)"><NumField label="Dark for seconds" value={cfg.sleep.dark_after_s} min={0} max={3600} onCommit={(v) => patchSleep({ dark_after_s: v })} /></Row>
                <Row label="Bright above (lx)"><NumField label="Bright above lux" value={cfg.sleep.bright_lux} min={0} max={10000} step={0.5} onCommit={(v) => patchSleep({ bright_lux: v })} /></Row>
                <Row label="… for (s)" hint="so car headlights do not count"><NumField label="Bright for seconds" value={cfg.sleep.bright_after_s} min={0} max={3600} onCommit={(v) => patchSleep({ bright_after_s: v })} /></Row>
              </details>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted mt-3">While asleep</div>
              <Row label="Screen off" hint="black screen and backlight off; a tap shows the clock for 10 s, a second tap the full face">
                <Switch label="Screen off" on={cfg.sleep.screen_off} onChange={(v) => patchSleep({ screen_off: v })} />
              </Row>
              {!cfg.sleep.screen_off && (
                <>
                  <Row label="Clock level" hint="how bright the amber digits are, on top of the backlight">
                    <CommitSlider label="Clock level" min={10} max={100} value={cfg.sleep.level_percent} onCommit={(v) => patchSleep({ level_percent: v })} />
                  </Row>
                  <Row label="Backlight" hint="blank = the backlight minimum">
                    <OptionalNumField label="Sleep backlight percent" placeholder="min" value={cfg.sleep.backlight_percent} min={0} max={100} onCommit={(v) => patchSleep({ backlight_percent: v })} />
                    <span className="text-sm text-muted">%</span>
                  </Row>
                  <Row label="Moves every (s)" hint="fades out, moves, fades in: no edge stays in one place">
                    <NumField label="Moves every seconds" value={cfg.sleep.jump_every_s} min={10} max={3600} onCommit={(v) => patchSleep({ jump_every_s: v })} />
                  </Row>
                </>
              )}
              <Row label="Tap brightness at night" hint="a tap while asleep or in the night palette; not a torch in the face">
                <NumField label="Tap brightness percent" value={cfg.sleep.wake_percent} min={1} max={100} onCommit={(v) => patchSleep({ wake_percent: v })} />
                <span className="text-sm text-muted">%</span>
              </Row>
              <div className="flex flex-wrap gap-2 mt-3">
                {d.sleep
                  ? <button className="btn btn-sm" onClick={() => sleepNow(false)}>Wake now</button>
                  : <button className="btn btn-sm" onClick={() => sleepNow(true)}>Sleep now</button>}
                <span className="text-xs text-muted self-center">holds until the next bedtime, morning, alarm or change of light</span>
              </div>
            </>
          )}
          {sleepErr && <p className="text-sm text-err mt-2">{sleepErr}</p>}
        </Card>
      )}

      {cfg && (
        <Card title="Burn-in protection">
          <p className="text-sm text-muted mb-2">The panel is an IPS LCD: the risk is a faint ghost of things that never move (image retention). These keep every edge moving.</p>
          <Row label="Pixel orbit" hint="the face drifts up to 8 px sideways and 6 px up and down, about a pixel a minute"><Switch label="Pixel orbit" on={cfg.burn_in.pixel_orbit} onChange={(v) => patchBurn({ pixel_orbit: v })} /></Row>
          <Row label="Hide the status strip in Standby" hint="seconds without a touch before it fades; a tap or a change worth seeing brings it back · 0 = never">
            <NumField label="Hide the status strip after seconds" value={cfg.burn_in.strip_autohide_s} min={0} max={3600} onCommit={(v) => patchBurn({ strip_autohide_s: v })} />
          </Row>
          <Row label="Background changes daily" hint="hills, trees and stars are drawn from the date"><Switch label="Background changes daily" on={cfg.burn_in.scene_daily} onChange={(v) => patchBurn({ scene_daily: v })} /></Row>
          <p className="text-xs text-muted mt-2">The sleep clock (above) moves on its own.</p>
          {burnErr && <p className="text-sm text-err mt-2">{burnErr}</p>}
        </Card>
      )}
    </div>
  );
}
