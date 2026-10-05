import { useState } from '../../shared/store';
import { Card, Row } from '../../shared/components';

const WIRING = [
  ['Encoder A / B', 'GPIO17 / GPIO27 (pins 11 / 13), middle pin to GND (pin 14) · internal pull-ups'],
  ['Encoder switch', 'GPIO22 (pin 15) ↔ GND (pin 20)'],
  ['Big button (optional)', 'GPIO23 (pin 16) ↔ GND, internal pull-up · off unless inputs.big_button.enabled'],
  ['VEML6030 SDA/SCL', 'GPIO2/GPIO3 (pins 3/5), 3V3 (pin 1), GND (pin 9) · address 0x10'],
  ['Audio Amp SHIM', 'I2S on GPIO18/19/21, 5V (pins 2/4), GPIO25 high · dtoverlay=hifiberry-dac'],
  ['RTL-SDR', 'USB'], ['u-blox 7 GPS', 'USB (/dev/ttyACM0)'], ['Waveshare 4.3" DSI', 'DSI ribbon + backlight via sysfs'],
];

export function About() {
  const s = useState();
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">About</h1>
      <Card>
        <div className="flex items-center gap-4">
          <img src="/favicon.svg" alt="" className="w-14 h-14" />
          <div><div className="text-xl font-semibold">Dawn</div><div className="text-muted text-sm">DAB+ alarm clock radio · v{s.system.version}{s.system.git_rev && ` (${s.system.git_rev})`}</div><div className="text-xs text-faint">{s.system.model}</div></div>
        </div>
      </Card>
      <Card title="Controls">
        <Row label="Big button (optional)" hint="short: stop alarm / stop playback / wake · hold 3 s: shut down" />
        <Row label="Encoder" hint="turn: volume · push: snooze (ringing) or next preset · hold: nap timer" />
        <Row label="Touch" hint="ringing: snooze (whole screen) · otherwise: menu" />
      </Card>
      <Card title="Wiring (defaults; pins are configurable)">
        {WIRING.map(([a, b]) => <Row key={a} label={a} hint={b} />)}
      </Card>
      <Card title="Built with">
        <div className="text-sm text-muted leading-relaxed">welle.io (DAB+), rtl-sdr-blog librtlsdr, mpv, PipeWire + WirePlumber, shairport-sync + nqptp (AirPlay 2), BlueZ, gpsd, chrony, FastAPI, SQLModel, React, Vite, Tailwind CSS, Inter, Open-Meteo, the <code>holidays</code> and <code>astral</code> Python packages.</div>
        <div className="text-xs text-faint mt-3">API docs: <a className="underline" href="/api/docs">/api/docs</a> · OpenAPI: <a className="underline" href="/api/openapi.json">/api/openapi.json</a></div>
      </Card>
    </div>
  );
}
