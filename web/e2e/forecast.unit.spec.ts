// Which forecast hour the standby scene draws, and what it draws for it (src/face/forecast.ts, Scene.tsx).
import { test, expect } from '@playwright/test';
import { hourKey, sceneWeather, upcomingHour } from '../src/face/forecast';
import { kindOf, moonPhase, seasonOf } from '../src/face/components/Scene';
import { EMPTY_STATE } from '../src/shared/types';
import type { WeatherHour } from '../src/shared/types';

const TZ = 'Australia/Sydney';
const hour = (time: string, icon: string, extra: Partial<WeatherHour> = {}): WeatherHour => ({
  time, code: null, icon, temperature: 20, cloud_cover: null, precip_probability: null, precipitation: null, wind_kmh: null, gusts_kmh: null, visibility_m: null, is_day: true, ...extra,
});
const hours = [hour('2026-10-10T14:00', 'clear-day'), hour('2026-10-10T15:00', 'thunder', { wind_kmh: 50 }), hour('2026-10-10T16:00', 'rain')];

test('the scene shows the hour coming up: this hour until half past, then the next', () => {
  expect(hourKey(new Date('2026-10-10T14:05:00+11:00'), TZ)).toBe('2026-10-10T14:00');
  expect(hourKey(new Date('2026-10-10T23:59:00+11:00'), TZ)).toBe('2026-10-10T23:00');
  expect(upcomingHour(hours, new Date('2026-10-10T14:10:00+11:00'), TZ)?.icon).toBe('clear-day');
  expect(upcomingHour(hours, new Date('2026-10-10T14:31:00+11:00'), TZ)?.icon).toBe('thunder');
  expect(upcomingHour(hours, new Date('2026-10-10T18:00:00+11:00'), TZ)).toBeNull();
  expect(upcomingHour([], new Date(), TZ)).toBeNull();
});

test('no forecast hour: the scene falls back to the current conditions, in °C', () => {
  const w = { ...EMPTY_STATE.weather, available: true, icon: 'fog', temperature: 50, units: 'fahrenheit', hours };
  const now = new Date('2026-10-11T09:00:00+11:00');
  expect(sceneWeather(w, now, TZ)).toMatchObject({ icon: 'fog', temperature: 10, at: null });
  expect(sceneWeather({ ...w, available: false }, now, TZ)).toBeNull();
  expect(sceneWeather(w, new Date('2026-10-10T14:45:00+11:00'), TZ)).toMatchObject({ icon: 'thunder', wind: 50, at: '2026-10-10T15:00' });
});

test('each kind of weather has its own picture', () => {
  const k = (icon: string, cloud: number | null = null) => kindOf({ icon, cloud, code: null, temperature: null, precip: null, precipProb: null, wind: null, gusts: null, visibility: null, at: null });
  expect(k('clear-day')).toBe('clear');
  expect(k('clear-night', 40)).toBe('partly');
  expect(k('partly-day', 80)).toBe('cloudy');
  expect(k('cloudy')).toBe('overcast');
  expect(['fog', 'drizzle', 'rain', 'thunder', 'hail', 'snow'].map((i) => k(i))).toEqual(['fog', 'drizzle', 'rain', 'storm', 'hail', 'snow']);
  expect(kindOf(null)).toBe('clear');
});

test('southern seasons and the moon', () => {
  expect(seasonOf(1, -35.3)).toBe('summer');
  expect(seasonOf(7, -35.3)).toBe('winter');
  expect(seasonOf(10, -35.3)).toBe('spring');
  expect(seasonOf(10, 51.5)).toBe('autumn');
  expect(moonPhase(new Date('2000-01-06T18:14:00Z'))).toBeCloseTo(0, 5);
  expect(moonPhase(new Date('2026-10-26T04:12:00Z'))).toBeGreaterThan(0.45); // full moon, 26 Oct 2026
  expect(moonPhase(new Date('2026-10-26T04:12:00Z'))).toBeLessThan(0.55);
});
