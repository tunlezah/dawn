import { useState } from '../../shared/store';
import { Card, Row } from '../../shared/components';

const WIRING = [
  ['KY-040 CLK', 'GPIO17 (pin 11)'], ['KY-040 DT', 'GPIO27 (pin 13)'], ['KY-040 SW', 'GPIO22 (pin 15)'], ['KY-040 +', '3V3 (pin 1)'], ['KY-040 GND', 'GND (pin 9)'],
  ['Arcade button', 'GPIO23 (pin 16) ↔ GND (pin 14), internal pull-up'],
  ['VEML6030 SDA/SCL', 'GPIO2/GPIO3 (pins 3/5), 3V3, GND · address 0x10'],
  ['HiFiBerry MiniAmp', 'HAT header (I2S), dtoverlay=hifiberry-dac'],
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
        <Row label="Big button" hint="short: stop alarm / stop playback / wake · hold 3 s: shut down" />
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
