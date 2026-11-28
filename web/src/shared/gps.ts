import type { GpsInfo } from './types';

/** How the GPS shows wherever there is a dot or a chip for it (face strip, Home, Status, Settings). GPS is optional:
 *  no module is a hollow yellow ring ("not fitted"), never the amber or red of a problem; a module that was there and
 *  has gone is amber; with GPS switched off nothing shows. */
export type GpsLook = { dot: 'ok' | 'off' | 'warn' | 'absent'; chip: string; title: string };

export function gpsLook(g: GpsInfo, live = false): GpsLook | null {
  switch (g.presence) {
    case 'off': return null;
    case 'absent': return { dot: 'absent', chip: 'No GPS module', title: 'no GPS module' };
    case 'missing': return { dot: 'warn', chip: 'Missing', title: 'GPS module missing' };
    default:
      return { dot: live || g.fix >= 2 ? 'ok' : 'off', chip: g.fix >= 3 ? '3D fix' : g.fix === 2 ? '2D fix' : 'No fix', title: g.fix >= 2 ? 'GPS fix' : 'no fix yet' };
  }
}

/** Chip colours to match the dot. */
export function gpsChipClass(look: GpsLook | null): string {
  if (!look) return 'chip';
  if (look.dot === 'absent') return 'chip text-absent border-absent';
  if (look.dot === 'warn') return 'chip text-warn border-warn';
  return 'chip';
}
