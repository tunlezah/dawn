// Which weather the standby scene draws: the forecast hour coming up, not the reading from the last fetch.
// Core publishes the next day of Open-Meteo hours; the face picks the slot nearest half an hour from now
// (at 14:10 the 14:00 hour, at 14:40 the 15:00 hour), so the picture changes on the hour by itself, even
// between fetches or while offline. With no usable hour it falls back to the current conditions.
import type { WeatherHour, WeatherState } from '../shared/types';
import { DEMO_WEATHER } from '../shared/demo';

/** Everything the scene draws from. Temperatures in °C whatever the display units. */
export interface SceneWeather {
  icon: string | null; code: number | null; temperature: number | null; cloud: number | null; precip: number | null;
  precipProb: number | null; wind: number | null; gusts: number | null; visibility: number | null; at: string | null;
}

/** "2026-10-10T15:00": the local hour `d` falls in, in Open-Meteo's hourly time format. */
export function hourKey(d: Date, tz: string): string {
  const p = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23' }).formatToParts(d);
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? '00';
  return `${g('year')}-${g('month')}-${g('day')}T${g('hour')}:00`;
}

const toC = (t: number | null, units: string) => (t === null || t === undefined ? null : units === 'fahrenheit' ? ((t - 32) * 5) / 9 : t);

/** The hour slot the scene shows at `now`, or null when the list has none for it. */
export function upcomingHour(hours: WeatherHour[] | undefined, now: Date, tz: string): WeatherHour | null {
  if (!hours?.length) return null;
  const key = hourKey(new Date(now.getTime() + 30 * 60_000), tz);
  return hours.find((h) => h.time === key) ?? null;
}

export function sceneWeather(w: WeatherState, now: Date, tz: string): SceneWeather | null {
  if (DEMO_WEATHER) {
    const d = DEMO_WEATHER;
    return { icon: d.icon, code: null, temperature: toC(d.temperature, w.units), cloud: d.cloud, precip: d.rain, precipProb: null, wind: d.wind, gusts: null, visibility: d.vis, at: null };
  }
  if (!w.available) return null;
  const h = upcomingHour(w.hours, now, tz);
  if (h) {
    return {
      icon: h.icon, code: h.code, temperature: toC(h.temperature ?? w.temperature, w.units), cloud: h.cloud_cover, precip: h.precipitation,
      precipProb: h.precip_probability, wind: h.wind_kmh, gusts: h.gusts_kmh, visibility: h.visibility_m, at: h.time,
    };
  }
  return { icon: w.icon, code: w.code, temperature: toC(w.temperature, w.units), cloud: null, precip: null, precipProb: null, wind: null, gusts: null, visibility: null, at: null };
}
