import type { UIState } from './types';

/** The evening check of the next alarm, when it is about that alarm: "✓" when ready, or what it will start on. */
export function eveningNote(s: Pick<UIState, 'alarms'>): { ok: boolean | null; text: string } | null {
  const ec = s.alarms.evening_check;
  const next = s.alarms.next;
  if (!ec || !next || ec.alarm_id !== next.id || ec.at !== next.at) return null;
  if (ec.ok) return { ok: true, text: '✓' };
  if (ec.ok === false) return { ok: false, text: ec.message };
  return null;  // checked again before it rings: nothing to worry about tonight
}
