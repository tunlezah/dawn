// The face's own safety net for alarms. It needs nothing from core's audio, only Chromium's:
//  - core asks for it (`ringing.face_beep`: the alarm is on its backup tone, so core's own sound may not be getting
//    out), and the face beeps along;
//  - core does not answer when an alarm is due (crashed, hung, or restarting for too long), or answers but cannot
//    read its alarms (`alarms.degraded`: the database on the SD card is unreadable): the face rings by itself, from
//    the last next-alarm time and ring it heard. That is kept in localStorage, so a reload of the page still knows
//    it, and a core that has lost its alarms does not overwrite it with nothing.
// The kiosk runs Chromium with --autoplay-policy=no-user-gesture-required, so it can sound without a touch. The
// loudness is the speaker's volume as core last set it; while core is down nothing can change it.

import { useEffect, useRef, useState } from 'react';
import type { UIState } from '../shared/types';

export const STALE_MS = 20_000; // no state from core for this long, and no answer from /api/health: core is down
export const LOCAL_AFTER_MS = 45_000; // how long the face waits for core past an alarm (or a lost ring) before ringing
export const LOCAL_MAX_MS = 30 * 60_000; // how long it rings by itself at most
export const LOCAL_SNOOZE_MS = 9 * 60_000;
const KEY = 'dawn.face.backup';
export const DOWN_WHY = 'Dawn is not responding';
export const DEGRADED_WHY = 'Dawn cannot read its alarms';

/** What the face last heard from core about alarms. */
export interface Heard {
  next: { id: number; label: string; at: string } | null;
  // `beep`: core had asked the face to beep along (its own sound may not be getting out)
  ring: { label: string; started: string; snoozedUntil: string | null; beep?: boolean } | null;
  at: number; // when (ms)
}

/** A ring the face is running by itself; `why` says what is wrong with core. */
export interface LocalRing { key: string; label: string; since: number; snoozedUntil: number | null; why: string }

/** The ring the face owes while core is down, or null. Pure: `heard` is the last thing core said, `silenced` the
 *  rings stopped on the face already. */
export function owedRing(heard: Heard | null, now: number, silenced: readonly string[]): { key: string; label: string; due: number } | null {
  if (!heard) return null;
  const cands: { key: string; label: string; due: number }[] = [];
  if (heard.ring) {
    // core went away while ringing (rings again 45 s later; at once if the face was already beeping along, so there
    // is no silent gap), or while snoozed (rings 45 s after the snooze's end)
    const snooze = heard.ring.snoozedUntil ? Date.parse(heard.ring.snoozedUntil) : NaN;
    const wait = heard.ring.beep && !Number.isFinite(snooze) ? 0 : LOCAL_AFTER_MS;
    cands.push({ key: `ring:${heard.ring.started}`, label: heard.ring.label, due: (Number.isFinite(snooze) ? Math.max(snooze, heard.at) : heard.at) + wait });
  }
  if (heard.next) {
    const at = Date.parse(heard.next.at);
    if (Number.isFinite(at)) cands.push({ key: `alarm:${heard.next.id}:${heard.next.at}`, label: heard.next.label, due: at + LOCAL_AFTER_MS });
  }
  return cands.find((c) => now >= c.due && now <= c.due + LOCAL_MAX_MS && !silenced.includes(c.key)) ?? null;
}

/** What core's state says to remember. A core that cannot read its alarms (`degraded`) knows nothing about them:
 *  what was heard before stays. */
export function hear(s: UIState, now: number, prev: Heard | null = null): Heard {
  const r = s.alarms.ringing;
  return {
    next: s.alarms.degraded ? prev?.next ?? null : s.alarms.next ? { id: s.alarms.next.id, label: s.alarms.next.label, at: s.alarms.next.at } : null,
    ring: r ? { label: r.label, started: r.started_at, snoozedUntil: r.snoozed_until, beep: r.face_beep && !r.snoozed_until } : null,
    at: now,
  };
}

interface Saved { heard: Heard | null; silenced: string[] }

function load(): Saved {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (v && typeof v === 'object') return { heard: v.heard ?? null, silenced: Array.isArray(v.silenced) ? v.silenced.slice(-20) : [] };
  } catch { /* private mode, or nothing saved */ }
  return { heard: null, silenced: [] };
}

function save(v: Saved) {
  try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* nothing to do */ }
}

/** The beep pattern in Web Audio: four 120 ms beeps at 2 kHz, 80 ms apart, then 520 ms of quiet; as core's tone. */
export class Beeper {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private timer: number | null = null;
  private until = 0;

  get running(): boolean { return this.timer !== null; }

  start() {
    if (this.timer !== null) return;
    try {
      this.ctx ??= new AudioContext();
    } catch {
      return; // no audio in this browser
    }
    const ctx = this.ctx;
    ctx.resume().catch(() => {});
    this.master = ctx.createGain();
    this.master.gain.value = 0.5;
    this.master.connect(ctx.destination);
    this.until = ctx.currentTime + 0.05;
    const schedule = () => {
      while (this.master && this.until < ctx.currentTime + 2) this.until = this.cycle(ctx, this.master, Math.max(this.until, ctx.currentTime + 0.02));
    };
    schedule();
    this.timer = window.setInterval(schedule, 500);
  }

  stop() {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    try { this.master?.disconnect(); } catch { /* already gone */ }
    this.master = null;
    this.ctx?.suspend().catch(() => {}); // no idle output stream held open
  }

  private cycle(ctx: AudioContext, out: GainNode, t: number): number {
    for (let i = 0; i < 4; i++) {
      const osc = ctx.createOscillator();
      const env = ctx.createGain();
      osc.frequency.value = 2000;
      env.gain.setValueAtTime(0, t);
      env.gain.linearRampToValueAtTime(1, t + 0.005);
      env.gain.setValueAtTime(1, t + 0.115);
      env.gain.linearRampToValueAtTime(0, t + 0.12);
      osc.connect(env).connect(out);
      osc.start(t);
      osc.stop(t + 0.13);
      t += 0.2;
    }
    return t + 0.52;
  }
}

/** Is core answering? The WebSocket says so while state keeps coming; otherwise /api/health decides, so a face whose
 *  own socket is stuck does not ring over a core that is fine. */
function useCoreUp(connected: boolean, version: number): boolean {
  const lastState = useRef(Date.now());
  const lastHealth = useRef(Date.now());
  const [up, setUp] = useState(true);
  useEffect(() => { lastState.current = Date.now(); }, [version]);
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      const now = Date.now();
      const fresh = connected && now - lastState.current < STALE_MS;
      if (fresh) lastHealth.current = now; // so core counts as down only STALE_MS after it was last heard, not at once
      else {
        const ctl = new AbortController();
        const t = window.setTimeout(() => ctl.abort(), 3000);
        try {
          const r = await fetch('/api/health', { cache: 'no-store', signal: ctl.signal });
          if (r.ok) lastHealth.current = Date.now();
        } catch { /* no answer */ }
        clearTimeout(t);
      }
      if (!stopped) setUp(fresh || Date.now() - lastHealth.current < STALE_MS);
    };
    const id = window.setInterval(check, 5000);
    check();
    return () => { stopped = true; clearInterval(id); };
  }, [connected]);
  return up;
}

export interface Backup {
  /** A ring the face is running by itself (core is down), or null. */
  local: LocalRing | null;
  /** The beeper is sounding. */
  beeping: boolean;
  snooze: () => void;
  stop: () => void;
}

export function useBackup(s: UIState, connected: boolean): Backup {
  const coreUp = useCoreUp(connected, s.version);
  const saved = useRef<Saved>(load());
  const [local, setLocal] = useState<LocalRing | null>(null);
  const [tick, setTick] = useState(0);
  const beeper = useRef<Beeper | null>(null);

  // remember what core says about alarms, while it says anything (EMPTY_STATE before the first message is not core).
  // When it was last heard is kept fresh in memory; localStorage is written only when the alarms change (an SD card).
  const persisted = useRef('');
  useEffect(() => {
    if (!connected || s.version === 0) return;
    const heard = hear(s, Date.now(), saved.current.heard);
    saved.current = { ...saved.current, heard };
    const what = JSON.stringify([heard.next, heard.ring]);
    if (what !== persisted.current) {
      persisted.current = what;
      save(saved.current);
    }
  }, [connected, s.version]);

  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // start a ring of its own when core is down (or up but without its alarms) and one is owed; hand it back when
  // core rings again (on state that came after the face's ring began: the state from before the outage says
  // nothing); give up at the max
  const stateAt = useRef(0);
  useEffect(() => { if (connected && s.version > 0) stateAt.current = Date.now(); }, [connected, s.version]);
  const degraded = coreUp && connected && s.alarms.degraded;
  useEffect(() => {
    const now = Date.now();
    if (local) {
      if (connected && stateAt.current > local.since && s.alarms.ringing) setLocal(null);
      else if (now - local.since > LOCAL_MAX_MS) setLocal(null);
      return;
    }
    if (coreUp && !degraded) return;
    const owed = owedRing(saved.current.heard, now, saved.current.silenced);
    if (owed) setLocal({ key: owed.key, label: owed.label, since: now, snoozedUntil: null, why: degraded ? DEGRADED_WHY : DOWN_WHY });
  }, [tick, coreUp, degraded, connected, s.alarms.ringing, local]);

  const r = s.alarms.ringing;
  const coreBeep = coreUp && !!r && r.face_beep && !r.snoozed_until;
  const localBeep = !!local && !(local.snoozedUntil && Date.now() < local.snoozedUntil);
  const beeping = coreBeep || localBeep;
  useEffect(() => {
    if (beeping) (beeper.current ??= new Beeper()).start();
    else beeper.current?.stop();
  }, [beeping]);
  useEffect(() => () => beeper.current?.stop(), []);

  const snooze = () => setLocal((l) => (l ? { ...l, snoozedUntil: Date.now() + LOCAL_SNOOZE_MS } : l));
  const stop = () => {
    if (local) {
      saved.current = { ...saved.current, silenced: [...saved.current.silenced, local.key].slice(-20) };
      save(saved.current);
    }
    setLocal(null);
  };
  return { local, beeping, snooze, stop };
}
