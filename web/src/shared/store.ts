// WebSocket state store with reconnect. Consumers use `useDawn()`.
import { useSyncExternalStore } from 'react';
import { EMPTY_STATE, type UIState } from './types';

type Listener = () => void;
export const HEARTBEAT_MS = 15_000;

class DawnStore {
  state: UIState = EMPTY_STATE;
  connected = false;
  private listeners = new Set<Listener>();
  private ws: WebSocket | null = null;
  private retry = 500;
  private timer: number | null = null;
  private heartbeat: number | null = null;
  private started = false;
  /** Sent to core on connect ("face" for the kiosk) so Diagnostics can tell the face is up. */
  private role: string | null = null;
  /** The web build this page was loaded from, as core first named it. */
  private build: string | null = null;
  /** The face is ringing by itself (core was down at alarm time): set by the face. That ring lives in this page
   *  only, so loading the page again would end it, and core, back up, no longer owes one. */
  localRing = false;

  start(role: string | null = null) {
    if (this.started) return;
    this.started = true;
    this.role = role;
    this.connect();
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && (!this.ws || this.ws.readyState !== WebSocket.OPEN)) this.connect();
    });
  }

  private connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/ws`;
    try {
      this.ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.connected = true; this.retry = 500; this.emit();
      if (this.role) this.ws?.send(JSON.stringify({ type: 'hello', role: this.role }));
      // a ping from the page itself every 15 s: the browser answers protocol pings even for a frozen page, so
      // this is what tells core the kiosk is alive (it restarts the kiosk when the face goes quiet)
      this.stopHeartbeat();
      this.heartbeat = window.setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) { try { this.ws.send(JSON.stringify({ type: 'ping' })); } catch { /* closing */ } }
      }, HEARTBEAT_MS);
    };
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'state') { this.state = msg.data as UIState; this.emit(); this.followBuild(); }
      } catch { /* ignore */ }
    };
    this.ws.onclose = () => { this.stopHeartbeat(); this.connected = false; this.emit(); this.scheduleReconnect(); };
    this.ws.onerror = () => { try { this.ws?.close(); } catch { /* ignore */ } };
  }

  /** An update replaces the files this page was built from and restarts core, but a page that is open (the kiosk
   *  runs for months) would carry on with its old scripts against the new core. Core names the build on disk in
   *  its state: when that changes, load the page again. Never while an alarm rings, core's or the face's own (the
   *  reload would cut the face's beep), nor while something is being typed: it then waits for a later message. */
  private followBuild() {
    const build = this.state.system?.web_build;
    if (!build) return;
    if (this.build === null) { this.build = build; return; }
    if (build === this.build || !shouldReload(this.localRing || this.state.alarms?.ringing != null, document.activeElement)) return;
    this.build = build; // once: if the reload does not happen, do not ask for it on every message
    location.reload();
  }

  private stopHeartbeat() {
    if (this.heartbeat !== null) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }

  private scheduleReconnect() {
    if (this.timer) return;
    this.timer = window.setTimeout(() => { this.timer = null; this.connect(); }, this.retry);
    this.retry = Math.min(this.retry * 2, 8000);
  }

  subscribe = (fn: Listener) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  private emit() { this.snapshot = { state: this.state, connected: this.connected }; for (const l of this.listeners) l(); }
  snapshot = { state: this.state, connected: this.connected };
  getSnapshot = () => this.snapshot;
}

/** Whether a page whose build has been replaced may load the new one now. */
export function shouldReload(ringing: boolean, focused: Element | null): boolean {
  if (ringing) return false;
  const tag = focused?.tagName;
  return !(tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT');
}

export const store = new DawnStore();

export function useDawn(): { state: UIState; connected: boolean } {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

export function useState(): UIState { return useDawn().state; }
