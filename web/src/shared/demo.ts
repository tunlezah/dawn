// Demo overrides from the URL, used for screenshots and design review of the face:
//   /face?at=2026-04-14T06:30:00+10:00&weather=rain&temp=11&tmin=8&tmax=14
// `at` shifts the clock by a fixed offset (it keeps ticking); `weather` is an icon id from
// shared/icons/weather.tsx and stands for the hour coming up too; `wind` (km/h), `cloud` (%),
// `rain` (mm/h) and `vis` (m) refine the scene. Absent parameters leave the live values untouched.
const params = (() => { try { return new URLSearchParams(window.location.search); } catch { return new URLSearchParams(); } })();

const at = params.get('at') ? new Date(params.get('at') as string).getTime() : NaN;
export const DEMO_OFFSET_MS = Number.isNaN(at) ? 0 : at - Date.now();

export interface DemoWeather {
  icon: string; temperature: number | null; t_min: number | null; t_max: number | null;
  wind: number | null; cloud: number | null; rain: number | null; vis: number | null;
}
const num = (k: string) => (params.get(k) !== null && params.get(k) !== '' ? Number(params.get(k)) : null);
export const DEMO_WEATHER: DemoWeather | null = params.get('weather') ? { icon: params.get('weather') as string, temperature: num('temp'), t_min: num('tmin'), t_max: num('tmax'),
  wind: num('wind'), cloud: num('cloud'), rain: num('rain'), vis: num('vis') } : null;
