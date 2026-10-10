import type { ReactNode } from 'react';

export function Card({ title, children, className = '', action }: { title?: string; children: ReactNode; className?: string; action?: ReactNode }) {
  return (
    <section className={`card p-4 ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between mb-3">
          {title && <h2 className="text-sm font-semibold tracking-wide uppercase text-muted">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className="switch" data-on={on} onClick={() => onChange(!on)}>
      <span />
    </button>
  );
}

export function Row({ label, hint, children, stack = false }: { label: string; hint?: string; children?: ReactNode; stack?: boolean }) {
  return (
    <div className={`py-2.5 border-b border-border last:border-0 ${stack ? 'space-y-2' : 'flex items-center justify-between gap-4'}`}>
      <div className="min-w-0">
        <div className="text-fg">{label}</div>
        {hint && <div className={`text-xs text-muted ${stack ? '' : 'truncate'}`}>{hint}</div>}
      </div>
      <div className={`flex items-center gap-2 ${stack ? 'flex-wrap' : 'shrink-0'}`}>{children}</div>
    </div>
  );
}

export function Slider({ value, min = 0, max = 100, step = 1, onChange, onCommit, label }: { value: number; min?: number; max?: number; step?: number; onChange?: (v: number) => void; onCommit?: (v: number) => void; label?: string }) {
  return (
    <input type="range" aria-label={label} min={min} max={max} step={step} value={value}
      onChange={(e) => onChange?.(Number(e.target.value))}
      onPointerUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))}
      onKeyUp={(e) => onCommit?.(Number((e.target as HTMLInputElement).value))} />
  );
}

export function Dot({ state }: { state: 'ok' | 'off' | 'warn' | 'err' }) {
  return <span className={`dot dot-${state}`} />;
}

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60" />
      <div className="relative w-full sm:max-w-lg max-h-[92vh] overflow-y-auto card rounded-b-none sm:rounded-b-2xl p-5 fade-in" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-center text-muted py-8 text-sm">{children}</div>;
}

export function Spinner() {
  return <span className="inline-block w-4 h-4 border-2 border-muted border-t-accent rounded-full animate-spin" />;
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/** "HH:MM" (24 h) picked as hour / minute (/ am-pm) selects, so it follows the clock setting rather than the
 *  browser's locale like <input type="time"> does. */
export function ClockInput({ value, onChange, h24, label, disabled, className = '' }: { value: string; onChange: (v: string) => void; h24: boolean; label: string; disabled?: boolean; className?: string }) {
  const [h, m] = /^\d{1,2}:\d{2}$/.test(value) ? value.split(':').map(Number) : [7, 0];
  const pm = h >= 12;
  const set = (hour: number, minute: number) => onChange(`${pad2(hour)}:${pad2(minute)}`);
  const hours = h24 ? [...Array(24).keys()] : [12, ...Array.from({ length: 11 }, (_, i) => i + 1)];
  return (
    <span className={`inline-flex items-center gap-1 tnum ${className}`} role="group" aria-label={label}>
      <select aria-label={`${label} hour`} disabled={disabled} className="!w-auto" value={h24 ? h : h % 12 || 12}
        onChange={(e) => { const v = Number(e.target.value); set(h24 ? v : (v % 12) + (pm ? 12 : 0), m); }}>
        {hours.map((x) => <option key={x} value={x}>{h24 ? pad2(x) : x}</option>)}
      </select>
      <span>:</span>
      <select aria-label={`${label} minute`} disabled={disabled} className="!w-auto" value={m} onChange={(e) => set(h, Number(e.target.value))}>
        {[...Array(60).keys()].map((x) => <option key={x} value={x}>{pad2(x)}</option>)}
      </select>
      {!h24 && (
        <select aria-label={`${label} am or pm`} disabled={disabled} className="!w-auto" value={pm ? 'pm' : 'am'}
          onChange={(e) => set((h % 12) + (e.target.value === 'pm' ? 12 : 0), m)}>
          <option value="am">am</option><option value="pm">pm</option>
        </select>
      )}
    </span>
  );
}
