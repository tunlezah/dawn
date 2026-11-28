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
  /** When requestAnimationFrame last called back (see the ping in connect()). */
  private lastFrame = Date.now();

  start(role: string | null = null) {
    if (this.started) return;
    this.started = true;
    this.role = role;
    window.setInterval(() => requestAnimationFrame(() => { this.lastFrame = Date.now(); }), 5_000);
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
      // The ping carries how long ago the page last drew a frame: requestAnimationFrame is asked for one every 5 s, so
      // a JavaScript that still runs while the compositor or GPU is stuck (a frozen screen) shows as a growing age.
      this.stopHeartbeat();
      this.heartbeat = window.setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          try { this.ws.send(JSON.stringify({ type: 'ping', raf_age_ms: Math.round(Date.now() - this.lastFrame) })); } catch { /* closing */ }
        }
      }, HEARTBEAT_MS);
    };
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'state') { this.state = msg.data as UIState; this.emit(); }
      } catch { /* ignore */ }
    };
    this.ws.onclose = () => { this.stopHeartbeat(); this.connected = false; this.emit(); this.scheduleReconnect(); };
    this.ws.onerror = () => { try { this.ws?.close(); } catch { /* ignore */ } };
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

export const store = new DawnStore();

export function useDawn(): { state: UIState; connected: boolean } {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

export function useState(): UIState { return useDawn().state; }
