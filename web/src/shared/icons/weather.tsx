// Bundled weather icon set (WMO code families), day/night variants. Pure SVG, inherits currentColor.
import type { ReactElement, SVGProps } from 'react';

type P = SVGProps<SVGSVGElement> & { size?: number };

const base = (size: number, p: P) => ({ width: size, height: size, viewBox: '0 0 64 64', fill: 'none', stroke: 'currentColor', strokeWidth: 3, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, ...p });

const Sun = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><circle cx="32" cy="32" r="11" /><path d="M32 8v6M32 50v6M8 32h6M50 32h6M15 15l4 4M45 45l4 4M15 49l4-4M45 19l4-4" /></svg>
);
const Moon = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M40 10a20 20 0 1 0 14 30 16 16 0 0 1-14-30z" /></svg>
);
const Cloud = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M20 48h26a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11.5 11.5 0 0 0 0 23z" /></svg>
);
const CloudSun = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><circle cx="22" cy="20" r="7" /><path d="M22 6v3M8 20h3M12 10l2 2M34 10l-2 2" /><path d="M26 54h22a9 9 0 0 0 1-18 12 12 0 0 0-23-3 10 10 0 0 0 0 21z" /></svg>
);
const CloudMoon = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M26 8a10 10 0 1 0 8 15 8 8 0 0 1-8-15z" /><path d="M26 54h22a9 9 0 0 0 1-18 12 12 0 0 0-23-3 10 10 0 0 0 0 21z" /></svg>
);
const Fog = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M20 38h26a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11.5 11.5 0 0 0 0 23z" /><path d="M14 48h36M20 56h24" /></svg>
);
const Drizzle = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M20 40h26a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11.5 11.5 0 0 0 0 23z" /><path d="M24 48v2M32 48v2M40 48v2M28 56v2M36 56v2" /></svg>
);
const Rain = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M20 38h26a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11.5 11.5 0 0 0 0 23z" /><path d="M24 46l-3 8M34 46l-3 8M44 46l-3 8" /></svg>
);
const Snow = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M20 38h26a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11.5 11.5 0 0 0 0 23z" /><path d="M24 48v6M21 51h6M34 48v6M31 51h6M44 48v6M41 51h6" /></svg>
);
const Thunder = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M20 38h26a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11.5 11.5 0 0 0 0 23z" /><path d="M34 40l-6 10h8l-4 10" /></svg>
);
const Hail = ({ size = 48, ...p }: P) => (
  <svg {...base(size, p)}><path d="M20 38h26a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11.5 11.5 0 0 0 0 23z" /><circle cx="24" cy="50" r="2" /><circle cx="34" cy="54" r="2" /><circle cx="44" cy="50" r="2" /></svg>
);

export const ICONS: Record<string, (p: P) => ReactElement> = {
  'clear-day': Sun, 'clear-night': Moon,
  'partly-day': CloudSun, 'partly-night': CloudMoon,
  'cloudy': Cloud, 'fog': Fog, 'drizzle': Drizzle, 'rain': Rain, 'snow': Snow, 'thunder': Thunder, 'hail': Hail,
};

export function WeatherIcon({ icon, size = 48, className }: { icon: string | null; size?: number; className?: string }) {
  const C = ICONS[icon ?? ''] ?? Cloud;
  return <C size={size} className={className} />;
}

// WMO code -> icon family (also done server side; here for offline rendering of cached codes).
export function wmoIcon(code: number | null, isDay: boolean): string {
  if (code === null) return 'cloudy';
  if (code === 0) return isDay ? 'clear-day' : 'clear-night';
  if (code <= 2) return isDay ? 'partly-day' : 'partly-night';
  if (code === 3) return 'cloudy';
  if (code <= 49) return 'fog';
  if (code <= 57) return 'drizzle';
  if (code <= 67) return 'rain';
  if (code <= 77) return 'snow';
  if (code <= 82) return 'rain';
  if (code <= 86) return 'snow';
  if (code <= 99) return 'thunder';
  return 'cloudy';
}
