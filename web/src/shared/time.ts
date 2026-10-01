// Clock helpers: tick every second, format in the device time zone.
import { useEffect, useState } from 'react';

export function useNow(intervalMs = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const align = intervalMs >= 1000 ? intervalMs - (Date.now() % intervalMs) : intervalMs;
    let id: number;
    const t = window.setTimeout(() => {
      setNow(new Date());
      id = window.setInterval(() => setNow(new Date()), intervalMs);
    }, align);
    return () => { clearTimeout(t); if (id) clearInterval(id); };
  }, [intervalMs]);
  return now;
}

export function fmtTime(d: Date, tz: string, h24: boolean): { hm: string; ampm: string | null; sec: string } {
  const parts = new Intl.DateTimeFormat('en-AU', { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: !h24 }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  let h = get('hour');
  if (!h24) h = String(parseInt(h, 10));
  const hm = `${h}:${get('minute')}`;
  const ampm = h24 ? null : (get('dayPeriod') || '').toLowerCase();
  return { hm, ampm, sec: get('second') };
}

export function fmtDate(d: Date, tz: string): string {
  return new Intl.DateTimeFormat('en-AU', { timeZone: tz, weekday: 'long', day: 'numeric', month: 'long' }).format(d);
}

export function fmtShort(iso: string | null, tz: string, h24 = true): string {
  if (!iso) return '';
  const d = new Date(iso);
  return new Intl.DateTimeFormat('en-AU', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: !h24 }).format(d);
}

export function fmtDayTime(iso: string | null, tz: string, h24 = true): string {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const sameDay = new Intl.DateTimeFormat('en-AU', { timeZone: tz, dateStyle: 'short' }).format(d) === new Intl.DateTimeFormat('en-AU', { timeZone: tz, dateStyle: 'short' }).format(now);
  const t = fmtShort(iso, tz, h24);
  if (sameDay) return `Today ${t}`;
  const tomorrow = new Date(now.getTime() + 86400000);
  if (new Intl.DateTimeFormat('en-AU', { timeZone: tz, dateStyle: 'short' }).format(d) === new Intl.DateTimeFormat('en-AU', { timeZone: tz, dateStyle: 'short' }).format(tomorrow)) return `Tomorrow ${t}`;
  return `${new Intl.DateTimeFormat('en-AU', { timeZone: tz, weekday: 'short' }).format(d)} ${t}`;
}

export function fmtDuration(s: number): string {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

export function fmtIn(seconds: number): string {
  if (seconds < 60) return 'under a minute';
  const h = Math.floor(seconds / 3600), m = Math.round((seconds % 3600) / 60);
  if (h === 0) return `${m} min`;
  if (h < 24) return m ? `${h} h ${m} min` : `${h} h`;
  const d = Math.floor(h / 24);
  return `${d} d ${h % 24} h`;
}
