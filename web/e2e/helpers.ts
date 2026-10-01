export const HUB = process.env.DAWN_HUB || 'http://127.0.0.1:8099';
export const BASE = process.env.DAWN_URL || 'http://127.0.0.1:8080';

export async function post(path: string, body: unknown = {}, base = BASE) {
  const r = await fetch(base + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  return r;
}
export async function hub(body: Record<string, unknown>) {
  await fetch(HUB + '/set', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
}
export async function state(): Promise<any> {
  return (await fetch(BASE + '/api/state')).json();
}
export async function waitFor(pred: (s: any) => boolean, ms = 8000): Promise<any> {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const s = await state();
    if (pred(s)) return s;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error('condition not met: ' + pred.toString());
}
export async function reset() {
  await hub({ lux: 150, gps_fix: true, dab_sync: true, sdr_present: true, network_online: true, audio_flowing: true });
  await post('/api/alarms/stop');
  await post('/api/audio/standby');
  await fetch(BASE + '/api/timers/nap', { method: 'DELETE' });
  await fetch(BASE + '/api/timers/sleep', { method: 'DELETE' });
  await post('/api/face/menu', { open: false });
  await post('/api/face/demo', { mode: null });
}
