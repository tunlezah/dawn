// WebSocket state store with reconnect. Consumers use `useDawn()`.
import { useSyncExternalStore } from 'react';
import { EMPTY_STATE, type UIState } from './types';

type Listener = () => void;

class DawnStore {
  state: UIState = EMPTY_STATE;
  connected = false;
  private listeners = new Set<Listener>();
  private ws: WebSocket | null = null;
  private retry = 500;
  private timer: number | null = null;
  private started = false;
  /** Sent to core on connect ("face" for the kiosk) so Diagnostics can tell the face is up. */
  private role: string | null = null;

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
    };
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'state') { this.state = msg.data as UIState; this.emit(); }
      } catch { /* ignore */ }
    };
    this.ws.onclose = () => { this.connected = false; this.emit(); this.scheduleReconnect(); };
    this.ws.onerror = () => { try { this.ws?.close(); } catch { /* ignore */ } };
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
