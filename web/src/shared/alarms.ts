// How an alarm's sound is described in the control UI: which rung of its ladder is on (its own source, the chime,
// the backup tone), and whether the next alarm's preparation found it ready.
import type { AlarmPrep, RingingInfo, RingTier } from './types';

export const TIER_LABEL: Record<RingTier, string> = { source: 'its own source', chime: 'the chime', buzzer: 'the backup tone' };

/** "Chime fallback active · no DAB signal on 9C", "Backup tone: …", or the source while it plays as set. */
export function ringNote(r: RingingInfo): string {
  if (r.tier === 'buzzer') return `Backup tone${r.fallback_reason ? ` · ${r.fallback_reason}` : ''}`;
  if (r.fallback) return `Chime fallback active${r.fallback_reason ? ` · ${r.fallback_reason}` : ''}`;
  return r.audible === false ? `${r.source} · no audio yet` : r.source;
}

/** For the next-alarm card while the alarm is being prepared (alarm_defaults.prepare_minutes ahead), else null. */
export function prepNote(p: AlarmPrep | null, alarmId: number | undefined): { ok: boolean; text: string } | null {
  if (!p || p.alarm_id !== alarmId) return null;
  if (p.ready) return { ok: true, text: 'Ready to ring' };
  if (p.pending) return { ok: true, text: `Getting ready: ${p.problems.join('; ')}` };
  const where = p.start_tier === 'source' ? '' : ` It will start on ${TIER_LABEL[p.start_tier]}.`;
  return { ok: false, text: p.problems.join('; ') + '.' + where };
}
