import { Card } from '../../shared/components';
import { usePoll, type GpsFacts, type Live, type Satellite } from './api';
import { ColumnChart, Figure, fmtNum, fmtU, Meter, SkyPlot, Stat, Table, VIZ, type Tone } from './charts';
import { ActionButton, ago, CheckList, Facts, HistorySection, sinceIso, StatusWord, type ChartSpec } from './common';

// same thresholds as the checks (core/dawn_core/diagnostics/gps.py)
const STRONG_DBHZ = 30, STRONG_NEEDED = 4, HDOP_POOR = 5;

export const GPS_HISTORY: ChartSpec[] = [
  { title: 'Satellites', metrics: [{ key: 'gps.used', label: 'used in the fix' }, { key: 'gps.seen', label: 'in view' }], unit: '', yMin: 0, digits: 0 },
  { title: 'Signal (mean of the satellites used)', metrics: [{ key: 'gps.snr' }], unit: 'dBHz', refs: [{ y: STRONG_DBHZ, label: `${STRONG_DBHZ} dBHz strong` }], digits: 0 },
  { title: 'Fix', metrics: [{ key: 'gps.fix' }], unit: '', yMin: 0, yMax: 3, digits: 0, sub: '3 = 3D, 2 = 2D, 0 or 1 = none' },
  { title: 'Horizontal dilution of precision (HDOP)', metrics: [{ key: 'gps.hdop' }], unit: '', yMin: 0, refs: [{ y: HDOP_POOR, label: 'poor above 5' }], digits: 1, sub: 'lower is better; it rises when the satellites in use are bunched together' },
  { title: 'GPS time message delay', metrics: [{ key: 'gps.toff' }], unit: 'ms', digits: 0, sub: 'how long after the second the receiver’s time report arrives (chrony allows for it)' },
];

const gnssLetter: Record<string, string> = { GPS: 'G', GLONASS: 'R', Galileo: 'E', BeiDou: 'C', QZSS: 'J', SBAS: 'S', IMES: 'I', NavIC: 'I' };
const satName = (s: Satellite) => `${gnssLetter[s.gnss] ?? '?'}${s.prn}`;

export function GpsPanel({ tz, h24, hours, onHours }: { tz: string; h24: boolean; hours: number; onHours: (h: number) => void }) {
  const live = usePoll<Live<GpsFacts>>('/api/diag/live/gps', 1000);
  const f = live.data?.facts;
  const d = f?.detail;
  const sats = (d?.satellites ?? []).slice().sort((a, b) => (b.ss ?? 0) - (a.ss ?? 0));
  const heard = sats.filter((s) => s.ss);
  const strong = heard.filter((s) => (s.ss ?? 0) >= STRONG_DBHZ).length;
  const sigTone: Tone = !d ? 'off' : !heard.length ? (sats.length ? 'err' : 'off') : strong >= STRONG_NEEDED ? 'ok' : 'warn';
  const fixTone: Tone = !d ? 'off' : d.has_fix && d.mode >= 3 ? 'ok' : d.has_fix ? 'warn' : 'err';
  const st = (t: Tone) => (t === 'ok' ? 'ok' : t === 'warn' ? 'warn' : t === 'err' ? 'fail' : 'off') as 'ok' | 'warn' | 'fail' | 'off';

  return (
    <div className="space-y-4">
      <Card title="Live" action={<span className="text-xs text-muted">{live.error ? `⚠ ${live.error}` : 'updates every second'}</span>}>
        {!d ? <div className="text-sm text-muted">Reading the receiver…</div> : (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <Stat label="Fix" value={d.has_fix ? (d.mode >= 3 ? '3D' : '2D') : 'No fix'} sub={<StatusWord status={st(fixTone)} />} />
            <Stat label="Satellites used" value={`${d.sats_used} of ${d.sats_seen}`} sub="used in the fix · in view" />
            <Stat label="Strong satellites" value={`${strong}`} sub={`above ${STRONG_DBHZ} dBHz; ${STRONG_NEEDED} or more is reliable`}>
              <Meter value={Math.min(strong, 8)} min={0} max={8} tone={sigTone} label={`Satellites above ${STRONG_DBHZ} dBHz`} />
            </Stat>
            <Stat label="Mean signal (used)" value={fmtU(d.snr_used_avg, 'dBHz', 0)} sub={`best ${fmtU(d.snr_max, 'dBHz', 0)}`} />
            <Stat label="HDOP" value={fmtNum(d.hdop, 1)} sub={d.hdop !== null && d.hdop > HDOP_POOR ? 'poor geometry' : 'lower is better'} />
            <Stat label="Last report" value={ago(f?.last_msg_age_s)} sub={d.source === 'gpsd' ? `via gpsd${d.gpsd_version ? ` ${d.gpsd_version}` : ''}` : `serial ${d.device ?? ''}`} />
          </div>
        )}
      </Card>

      <Card title="Checks"><CheckList checks={live.data?.checks ?? []} onChanged={live.reload} /></Card>

      {d && (
        <Card title="Sky">
          {sats.length === 0 ? <div className="text-sm text-muted">The receiver has not reported any satellites yet.</div> : (
            <div className="grid gap-5 sm:grid-cols-2">
              <Figure title="Where they are" sub="Centre is straight up; the edge is the horizon. Buildings and walls block the low ones."
                legend={[{ label: 'used in the fix', color: VIZ.s1, kind: 'dot' }, { label: 'not used', color: VIZ.dim, kind: 'dot' }]} table={() => satTable(sats)}>
                <SkyPlot label="Satellite positions" dots={sats.filter((s) => s.el !== null && s.az !== null).map((s) => ({
                  key: `${s.gnss}${s.prn}`, az: s.az ?? 0, el: s.el ?? 0, color: s.used ? VIZ.s1 : VIZ.dim,
                  title: `${satName(s)} · ${s.gnss}`, detail: `${s.ss ? `${s.ss.toFixed(0)} dBHz` : 'not heard'} · elevation ${s.el?.toFixed(0)}° · azimuth ${s.az?.toFixed(0)}° · ${s.used ? 'used' : 'not used'}`,
                }))} />
              </Figure>
              <Figure title="Signal per satellite" sub={`Strongest first; ${STRONG_DBHZ} dBHz and above is strong. G GPS · R GLONASS · E Galileo · C BeiDou · S SBAS.`}
                legend={[{ label: 'used in the fix', color: VIZ.s1, kind: 'rect' }, { label: 'not used', color: VIZ.dim, kind: 'rect' }]} table={() => satTable(sats)}>
                <ColumnChart unit="dBHz" label="Signal strength per satellite" xTitle="Satellite" yMax={50} refs={[{ y: STRONG_DBHZ, label: `${STRONG_DBHZ} dBHz` }]}
                  cols={sats.map((s) => ({ key: `${s.gnss}${s.prn}`, label: `${satName(s)} (${s.gnss})`, value: s.ss, color: s.used ? VIZ.s1 : VIZ.dim,
                    note: `${s.used ? 'used' : 'not used'} · elevation ${s.el?.toFixed(0) ?? '–'}°` }))}
                  xLabel={(c) => c.label.split(' ')[0]} />
              </Figure>
            </div>
          )}
        </Card>
      )}

      {f && d && (
        <Card title="Receiver">
          <Facts rows={[
            ['Source', f.configured_source === 'sim' ? 'simulator' : `${d.source}${d.source === 'gpsd' ? ` (gpsd ${d.connected ? 'connected' : `not connected${d.connect_error ? `: ${d.connect_error}` : ''}`})` : ''}`],
            ['USB receiver', f.usb.length ? f.usb.map((u) => `${u.product ?? 'device'} (${u.vid}:${u.pid})${u.power ? `, draws up to ${u.power}` : ''}`).join('; ') : 'none found'],
            ['Device', `${d.device ?? '–'}${Object.keys(f.paths).length ? ` · ${Object.entries(f.paths).map(([p, ok]) => `${p} ${ok ? 'present' : 'missing'}`).join(', ')}` : ''}`],
            ['Driver', [d.driver, d.subtype].filter(Boolean).join(' · ') || '–'],
            ['Serial speed', d.bps ? `${d.bps} bit/s` : '–'],
            ['gpsd service', f.unit ?? '–'],
            ['Activated', d.activated ? sinceIso(d.activated) : '–'],
            ['Position', d.lat !== null && d.lon !== null ? `${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}${d.alt !== null ? ` · ${d.alt.toFixed(0)} m` : ''}` : '–'],
            ['Estimated error', [d.eph !== null ? `±${d.eph.toFixed(0)} m horizontal` : null, d.epv !== null ? `±${d.epv.toFixed(0)} m vertical` : null, d.ept !== null ? `±${(d.ept * 1000).toFixed(0)} ms time` : null].filter(Boolean).join(' · ') || '–'],
            ['Dilution of precision', `HDOP ${fmtNum(d.hdop, 1)} · VDOP ${fmtNum(d.vdop, 1)} · PDOP ${fmtNum(d.pdop, 1)} · TDOP ${fmtNum(d.tdop, 1)}`],
            ['GPS time', d.time ?? '–'],
            ['Time message delay', d.toff_ms !== null ? `${fmtNum(-d.toff_ms, 0)} ms after the second (${ago(f.toff_age_s)})` : '–'],
            ['Location used', f.location_source === 'gps' ? 'GPS position' : `configured location${f.prefer_gps ? ' (GPS preferred once it has a fix)' : ''}`],
            ...(f.configured_distance_km !== null ? [['Configured location', `${fmtNum(f.configured_distance_km, 1)} km from the GPS position`] as [string, string]] : []),
            ...(d.error ? [['Last error', d.error] as [string, string]] : []),
          ]} />
          <div className="mt-3"><ActionButton id="gps.restart" label="Restart gpsd" onDone={live.reload} /></div>
        </Card>
      )}

      <Card title="History"><HistorySection specs={GPS_HISTORY} hours={hours} onHours={onHours} tz={tz} h24={h24} area="gps" /></Card>
    </div>
  );
}

function satTable(sats: Satellite[]) {
  return <Table head={['Satellite', 'System', 'Signal', 'Elevation', 'Azimuth', 'Used', 'Health']} rows={sats.map((s) => [
    satName(s), s.gnss, s.ss ? `${s.ss.toFixed(0)} dBHz` : 'not heard', s.el !== null ? `${s.el.toFixed(0)}°` : '–', s.az !== null ? `${s.az.toFixed(0)}°` : '–',
    s.used ? 'yes' : 'no', s.health === null ? '–' : s.health === 1 ? 'ok' : s.health === 2 ? 'unhealthy' : 'unknown',
  ])} />;
}
