// Thin REST client. All calls are relative so the same build serves from any host.

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) { super(message); }
}

async function call<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  });
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('dawn:unauthorized'));
  }
  if (!res.ok) {
    let detail: unknown = undefined;
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); detail = j.detail ?? j; if (typeof j.detail === 'string') msg = j.detail; } catch { /* ignore */ }
    throw new ApiError(res.status, msg, detail);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const api = {
  get: <T = unknown>(p: string) => call<T>('GET', p),
  post: <T = unknown>(p: string, b?: unknown) => call<T>('POST', p, b ?? {}),
  put: <T = unknown>(p: string, b?: unknown) => call<T>('PUT', p, b ?? {}),
  patch: <T = unknown>(p: string, b?: unknown) => call<T>('PATCH', p, b ?? {}),
  del: <T = unknown>(p: string) => call<T>('DELETE', p),
};

// Convenience wrappers used by both UIs.
export const actions = {
  input: (event: string, value?: unknown) => api.post('/api/input', { event, value }),
  touch: () => api.post('/api/face/touch'),
  snooze: () => api.post('/api/alarms/snooze'),
  stopRinging: () => api.post('/api/alarms/stop'),
  setVolume: (volume: number) => api.put('/api/audio/volume', { volume }),
  mute: (muted?: boolean) => api.post('/api/audio/mute', muted === undefined ? {} : { muted }),
  standby: () => api.post('/api/audio/standby'),
  play: (source: string) => api.post('/api/audio/play', { source }),
  stop: () => api.post('/api/audio/stop'),
  sleep: (minutes: number) => api.post('/api/timers/sleep', { minutes }),
  cancelSleep: () => api.del('/api/timers/sleep'),
  nap: (minutes: number) => api.post('/api/timers/nap', { minutes }),
  cancelNap: () => api.del('/api/timers/nap'),
  nextPreset: () => api.post('/api/presets/next'),
  prevPreset: () => api.post('/api/presets/prev'),
  addPreset: (label: string, source: string) => api.post('/api/presets', { label, source }),
  deletePreset: (id: number) => api.del(`/api/presets/${id}`),
  volumeStep: (direction: 1 | -1) => api.post('/api/audio/volume/step', { direction }),
  airplayRemote: (command: 'Play' | 'Pause' | 'PlayPause' | 'Next' | 'Previous' | 'Stop') => api.post(`/api/airplay/remote/${command}`),
  bluetoothPlayer: (command: 'Play' | 'Pause' | 'Stop' | 'Next' | 'Previous') => api.post(`/api/bluetooth/player/${command}`),
  brightness: (value: number) => api.put('/api/display/brightness', { value }),
  brightnessMode: (mode: 'auto' | 'manual') => api.put('/api/display/mode', { mode }),
  faceMenu: (open: boolean, page?: string) => api.post('/api/face/menu', { open, page: page ?? null }),
  menuActivity: () => api.post('/api/face/menu/activity'),
  dismissMessage: () => api.post('/api/face/dismiss'),
};
