// Generic editor rendered from the pydantic JSON schema: any option in config.yaml can be changed here.
import { useEffect, useState as useReactState } from 'react';
import { api, ApiError } from '../../shared/api';
import { Switch, Spinner } from '../../shared/components';

type Schema = { $defs?: Record<string, Schema>; properties?: Record<string, Schema>; type?: string | string[]; enum?: unknown[]; anyOf?: Schema[]; $ref?: string; description?: string; items?: Schema; minimum?: number; maximum?: number; default?: unknown; title?: string };

function resolve(s: Schema, root: Schema): Schema {
  if (s.$ref) { const name = s.$ref.replace('#/$defs/', ''); return resolve(root.$defs?.[name] ?? {}, root); }
  if (s.anyOf) { const nonNull = s.anyOf.filter((x) => x.type !== 'null'); if (nonNull.length === 1) return { ...resolve(nonNull[0], root), description: s.description ?? nonNull[0].description }; }
  return s;
}

function Field({ name, schema, value, onChange, root }: { name: string; schema: Schema; value: unknown; onChange: (v: unknown) => void; root: Schema }) {
  const s = resolve(schema, root);
  const types = Array.isArray(s.type) ? s.type : [s.type];
  const nullable = schema.anyOf?.some((x) => x.type === 'null');
  const label = <div className="min-w-0"><div className="text-sm">{name}</div>{s.description && <div className="text-xs text-muted">{s.description}</div>}</div>;
  let control;
  if (s.enum) {
    control = <select value={value === null || value === undefined ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? null : (typeof s.enum![0] === 'number' ? Number(e.target.value) : e.target.value))} className="w-44">{nullable && <option value="">(none)</option>}{s.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}</select>;
  } else if (types.includes('boolean')) {
    control = <Switch on={Boolean(value)} onChange={onChange} />;
  } else if (types.includes('integer') || types.includes('number')) {
    control = <input type="number" step={types.includes('integer') ? 1 : 'any'} min={s.minimum} max={s.maximum} className="w-32" value={value === null || value === undefined ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? (nullable ? null : 0) : Number(e.target.value))} />;
  } else if (types.includes('array')) {
    const items = resolve(s.items ?? {}, root);
    const isObj = items.type === 'object';
    control = <textarea className="w-56 text-xs font-mono" rows={2} defaultValue={JSON.stringify(value ?? [])} onBlur={(e) => { try { onChange(JSON.parse(e.target.value)); } catch { /* ignore */ } }} title={isObj ? 'JSON list of objects' : 'JSON list'} />;
  } else {
    control = <input type="text" className="w-56" value={value === null || value === undefined ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' && nullable ? null : e.target.value)} />;
  }
  return <div className="flex items-start justify-between gap-3 py-2 border-b border-border last:border-0">{label}<div className="shrink-0">{control}</div></div>;
}

function Section({ name, schema, value, root, onPatch }: { name: string; schema: Schema; value: Record<string, unknown>; root: Schema; onPatch: (path: string[], v: unknown) => void }) {
  const s = resolve(schema, root);
  const [open, setOpen] = useReactState(false);
  return (
    <details className="border border-border rounded-xl px-3 py-1 my-1" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer py-1.5 text-sm font-medium">{name}{s.description && <span className="text-muted font-normal"> · {s.description}</span>}</summary>
      {open && Object.entries(s.properties ?? {}).map(([k, sub]) => {
        const r = resolve(sub, root);
        if (r.type === 'object' && r.properties) return <Section key={k} name={k} schema={sub} value={(value?.[k] as Record<string, unknown>) ?? {}} root={root} onPatch={(p, v) => onPatch([k, ...p], v)} />;
        return <Field key={k} name={k} schema={sub} value={value?.[k]} root={root} onChange={(v) => onPatch([k], v)} />;
      })}
    </details>
  );
}

export function ConfigEditor({ onSaved }: { onSaved?: () => void }) {
  const [schema, setSchema] = useReactState<Schema | null>(null);
  const [cfg, setCfg] = useReactState<Record<string, unknown> | null>(null);
  const [err, setErr] = useReactState<string | null>(null);
  const load = () => Promise.all([api.get<Schema>('/api/config/schema'), api.get<Record<string, unknown>>('/api/config')]).then(([s, c]) => { setSchema(s); setCfg(c); });
  useEffect(() => { load(); }, []);
  if (!schema || !cfg) return <Spinner />;
  const onPatch = async (path: string[], v: unknown) => {
    const patch: Record<string, unknown> = {};
    let cur = patch;
    path.forEach((k, i) => { if (i === path.length - 1) cur[k] = v; else { cur[k] = {}; cur = cur[k] as Record<string, unknown>; } });
    try { await api.patch('/api/config', patch); setErr(null); await load(); onSaved?.(); } catch (e) { setErr(e instanceof ApiError ? `${path.join('.')}: ${JSON.stringify(e.detail)}` : String(e)); }
  };
  return (
    <div>
      {err && <div className="text-sm text-err mb-2">{err}</div>}
      {Object.entries(schema.properties ?? {}).map(([k, sub]) => {
        const r = resolve(sub, schema);
        if (r.type === 'object' || r.properties) return <Section key={k} name={k} schema={sub} value={(cfg[k] as Record<string, unknown>) ?? {}} root={schema} onPatch={(p, v) => onPatch([k, ...p], v)} />;
        return <Field key={k} name={k} schema={sub} value={cfg[k]} root={schema} onChange={(v) => onPatch([k], v)} />;
      })}
      <div className="text-xs text-faint mt-2">Changes are validated, written to config.yaml and applied live.</div>
    </div>
  );
}
