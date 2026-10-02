// Face icon set: plain SVG, inherits currentColor, sized by the parent via CSS (width/height on .f-icon).
import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement>;
const S = ({ children, ...p }: P) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="f-icon" aria-hidden="true" {...p}>{children}</svg>
);
const F = ({ children, ...p }: P) => (
  <svg viewBox="0 0 24 24" fill="currentColor" stroke="none" className="f-icon" aria-hidden="true" {...p}>{children}</svg>
);

export const IconPrev = (p: P) => <F {...p}><path d="M6 5h2.5v14H6zM18 5.5v13a.75.75 0 0 1-1.19.6L9 12.6a.75.75 0 0 1 0-1.2l7.81-6.5A.75.75 0 0 1 18 5.5z" /></F>;
export const IconNext = (p: P) => <F {...p}><path d="M15.5 5H18v14h-2.5zM6 5.5v13a.75.75 0 0 0 1.19.6L15 12.6a.75.75 0 0 0 0-1.2L7.19 4.9A.75.75 0 0 0 6 5.5z" /></F>;
export const IconPlay = (p: P) => <F {...p}><path d="M7 4.9v14.2a.9.9 0 0 0 1.38.76l11.1-7.1a.9.9 0 0 0 0-1.52L8.38 4.14A.9.9 0 0 0 7 4.9z" /></F>;
export const IconPause = (p: P) => <F {...p}><rect x="5.5" y="4.5" width="4.5" height="15" rx="1.2" /><rect x="14" y="4.5" width="4.5" height="15" rx="1.2" /></F>;
export const IconStop = (p: P) => <F {...p}><rect x="5" y="5" width="14" height="14" rx="2.5" /></F>;
export const IconStar = ({ filled = false, ...p }: P & { filled?: boolean }) => (
  <svg viewBox="0 0 24 24" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round" className="f-icon" aria-hidden="true" {...p}>
    <path d="M12 3.4l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 16.8l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8z" />
  </svg>
);
export const IconVolume = ({ level = 1, ...p }: P & { level?: 0 | 1 | 2 }) => (
  <S {...p}>
    <path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor" stroke="none" />
    {level >= 1 && <path d="M15.5 9.3a4 4 0 0 1 0 5.4" />}
    {level >= 2 && <path d="M18.3 6.5a8 8 0 0 1 0 11" />}
  </S>
);
export const IconMuted = (p: P) => <S {...p}><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor" stroke="none" /><path d="M16 9.5l5 5M21 9.5l-5 5" /></S>;
export const IconAlarm = (p: P) => <S {...p}><circle cx="12" cy="13" r="7.5" /><path d="M12 9.5V13l2.5 1.8M4.5 5.5l2.5-2M19.5 5.5l-2.5-2" /></S>;
export const IconAirplay = (p: P) => <S {...p}><path d="M5.5 16.2A7.5 7.5 0 1 1 18.5 16.2" /><path d="M8.3 13.9a4 4 0 1 1 7.4 0" /><path d="M8 20.5l4-5 4 5z" fill="currentColor" stroke="none" /></S>;
export const IconBluetooth = (p: P) => <S {...p}><path d="M7 8l10 8-5 4V4l5 4L7 16" /></S>;
export const IconRadio = (p: P) => <S {...p}><rect x="3" y="8" width="18" height="12" rx="2.5" /><circle cx="8.5" cy="14" r="2.5" /><path d="M14 12h4M14 16h4M6 8l10-4.5" /></S>;
export const IconMoon = (p: P) => <S {...p}><path d="M19.5 14.5A8 8 0 1 1 9.5 4.5a6.5 6.5 0 0 0 10 10z" /></S>;
export const IconNap = (p: P) => <S {...p}><path d="M14.5 15A7 7 0 1 1 8 4.8 5.5 5.5 0 0 0 14.5 15z" /><path d="M16 4h4l-4 4h4" strokeWidth={1.8} /></S>;
export const IconSun = (p: P) => <S {...p}><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" /></S>;
export const IconClose = (p: P) => <S {...p}><path d="M6 6l12 12M18 6L6 18" /></S>;
export const IconBack = (p: P) => <S {...p}><path d="M14.5 6l-6 6 6 6" /></S>;
export const IconPlus = (p: P) => <S {...p}><path d="M12 5v14M5 12h14" /></S>;
export const IconMinus = (p: P) => <S {...p}><path d="M5 12h14" /></S>;
export const IconWifiOff = (p: P) => <S {...p}><path d="M2 8.5a15 15 0 0 1 20 0M5.5 12a10 10 0 0 1 13 0M9 15.5a5 5 0 0 1 6 0" /><circle cx="12" cy="19" r="1" fill="currentColor" /><path d="M3 3l18 18" /></S>;
export const IconLight = (p: P) => <S {...p}><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z" /></S>;

// Four-bar signal meter; `percent` 0..100 lights 0..4 bars.
export function SignalBars({ percent, className = '' }: { percent: number | null; className?: string }) {
  const lit = percent === null ? 0 : percent >= 80 ? 4 : percent >= 55 ? 3 : percent >= 30 ? 2 : percent > 0 ? 1 : 0;
  return (
    <svg viewBox="0 0 24 24" className={`f-icon ${className}`} aria-hidden="true">
      {[0, 1, 2, 3].map((i) => (
        <rect key={i} x={3 + i * 5} y={16 - i * 4} width="3.4" height={5 + i * 4} rx="1" fill="currentColor" opacity={i < lit ? 1 : 0.22} />
      ))}
    </svg>
  );
}
