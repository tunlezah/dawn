import { Card } from '../../shared/components';
import type { Check, ChronySource, TimeFacts } from './api';
import { fmtNum, Stat, Table } from './charts';
import { ActionButton, ago, CheckList, Facts, HistorySection, sinceIso, StatusWord, type ChartSpec } from './common';

export const TIME_HISTORY: ChartSpec[] = [
  { title: 'System clock offset', metrics: [{ key: 'time.system' }], unit: 'ms', digits: 3, sub: 'how far chrony estimates the clock is from true time' },
  { title: 'Clock synchronised', metrics: [{ key: 'time.synced' }], unit: '%', yMin: 0, yMax: 100, digits: 0 },
  { title: 'GPS source offset', metrics: [{ key: 'time.gps' }], unit: 'ms', digits: 3 },
  { title: 'DAB source offset', metrics: [{ key: 'time.dab' }], unit: 'ms', digits: 1, sub: 'DAB time is only accurate to a few tens of ms; chrony weighs it accordingly' },
  { title: 'NTP source offset (best server)', metrics: [{ key: 'time.ntp' }], unit: 'ms', digits: 1 },
];

const sec = (s: number | null | undefined) => (s === null || s === undefined ? '–' : Math.abs(s) < 1e-3 ? `${fmtNum(s * 1e6, 0)} µs` : Math.abs(s) < 1 ? `${fmtNum(s * 1e3, 2)} ms` : `${fmtNum(s, 2)} s`);
const ms = (v: number | null | undefined) => (v === null || v === undefined ? '–' : sec(v / 1000));
const KIND: Record<string, string> = { gps: 'GPS', dab: 'DAB', ntp: 'NTP', pps: 'PPS', other: '' };

export function TimePanel({ facts, checks, onChanged, tz, h24, hours, onHours }: {
  facts: TimeFacts | null; checks: Check[]; onChanged: () => void; tz: string; h24: boolean; hours: number; onHours: (h: number) => void;
}) {
  const t = facts?.tracking ?? null;
  const used = facts?.sources.find((s) => s.selected);
  const timed = facts?.timed ?? null;
  return (
    <div className="space-y-4">
      <Card title="Clock">
        {!facts ? <div className="text-sm text-muted">Asking chrony…</div> : (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <Stat label="Synchronised" value={facts.synced ? 'Yes' : 'No'} sub={<StatusWord status={facts.synced ? 'ok' : 'fail'} />} />
            <Stat label="Source in use" value={used ? `${KIND[used.kind] || used.name}` : (t?.refname || '–')} sub={used ? used.name : 'none selected'} />
            <Stat label="System clock offset" value={sec(t?.system_offset_s)} sub={`last correction ${sec(t?.last_offset_s)} · RMS ${sec(t?.rms_s)}`} />
            <Stat label="Stratum" value={t ? String(t.stratum) : '–'} sub="1 = straight from a reference clock (GPS, DAB)" />
            <Stat label="Clock drift" value={t ? `${fmtNum(t.freq_ppm, 3)} ppm` : '–'} sub={t ? `residual ${fmtNum(t.resid_freq_ppm, 3)} · skew ${fmtNum(t.skew_ppm, 3)} ppm` : undefined} />
            <Stat label="Error bound" value={t ? sec(Math.abs(t.system_offset_s) + t.root_dispersion_s + t.root_delay_s / 2) : '–'} sub={t ? `updated every ${sec(t.update_interval_s)} · leap ${t.leap}` : undefined} />
          </div>
        )}
        {facts?.chronyc_error && <p className="text-sm text-err mt-2">chronyc: {facts.chronyc_error}</p>}
      </Card>

      <Card title="From each time source into chrony">
        <p className="text-xs text-muted mb-2">Each chain is followed step by step, from the hardware to chrony’s choice; the first step that fails is where to look.</p>
        <CheckList checks={checks} onChanged={onChanged} />
      </Card>

      {facts && facts.sources.length > 0 && (
        <Card title="chrony’s sources">
          <div className="overflow-x-auto">
            <Table head={['Source', 'State', 'Reach', 'Last sample', 'Offset ± error', 'Spread (std dev)', 'Options']} rows={facts.sources.map((s) => [
              `${s.name}${KIND[s.kind] && KIND[s.kind] !== s.name ? ` (${KIND[s.kind]})` : ''}`,
              `${s.state_label}`,
              `${s.reach_count}/8 (${s.reach})`,
              s.last_rx_s !== null ? ago(s.last_rx_s) : '–',
              s.offset_ms !== null ? `${ms(s.offset_ms)} ± ${ms(s.error_ms)}` : '–',
              s.stats ? `${sec(s.stats.std_dev_s)} over ${s.stats.samples} samples` : '–',
              s.select?.effective ? options(s.select.effective) : '–',
            ])} />
          </div>
          <ul className="text-xs text-muted mt-2 space-y-0.5">
            {facts.sources.map((s) => <li key={s.name}><b className="text-fg font-medium">{s.name}</b>: {s.reason}{ntpLine(s)}</li>)}
          </ul>
          {facts.select_error && <p className="text-xs text-muted mt-2">Selection details unavailable: {facts.select_error}</p>}
          {facts.ntpdata_error && <p className="text-xs text-muted mt-1">NTP packet details unavailable: {facts.ntpdata_error}</p>}
        </Card>
      )}

      {facts && (
        <Card title="DAB time (dawn-timed)">
          {!timed ? <div className="text-sm text-muted">{facts.timed_error ?? 'No status from dawn-timed.'}</div> : (
            <Facts rows={[
              ['Service', `${facts.units['dawn-timed'] ?? 'unknown'} · v${timed.version} · status ${ago(facts.timed_age_s)}`],
              ['Reading from', `${timed.welle_url} · ${timed.welle_reachable ? 'answering' : 'not answering'}`],
              ['Method', timed.mode === 'fic' ? 'FIC stream (FIG 0/10, millisecond time)' : timed.mode === 'mux' ? 'welle-cli mux.json (time to the minute)' : timed.mode],
              ['Ensemble time', timed.synced ? `decoding · last ${sinceIso(timed.last_dab_time)}` : 'no DAB time yet'],
              ['Offset of the last sample', timed.last_offset_ms !== null ? `${fmtNum(timed.last_offset_ms, 1)} ms vs the system clock` : '–'],
              ['Samples to chrony', `${timed.samples_written}${timed.last_sample_at ? `, last ${sinceIso(timed.last_sample_at)}` : ''}`],
              ['FIG 0/10 seen', `${timed.fig010_long} long form (ms) · ${timed.fig010_short} short form`],
              ['FIC blocks', `${timed.fibs} read · ${timed.fib_crc_errors} CRC failures`],
              ['Shared memory', `SHM unit ${timed.shm_unit}: ${timed.shm_attached ? 'attached' : 'not attached'}${timed.dry_run ? ' (dry run: nothing is written)' : ''}`],
              ...(timed.shm_error ? [['Shared memory problem', timed.shm_error] as [string, string]] : []),
              ...(timed.last_error ? [['Last error', timed.last_error] as [string, string]] : []),
            ]} />
          )}
          {facts.shm.length > 0 && (
            <div className="mt-3">
              <div className="text-sm font-medium mb-1">NTP shared-memory segments</div>
              <Table head={['Unit', 'Key', 'Permissions', 'Attached processes', 'Owner uid']} rows={facts.shm.map((s) => [s.unit, `0x${s.key.toString(16)}`, s.perms, s.nattch, s.uid])} />
              <p className="text-xs text-muted mt-1">chrony creates these for its <code>refclock SHM</code> lines. Unit 0 is GPS from gpsd, unit 2 is DAB from dawn-timed (which needs <code>perm=0666</code>).</p>
            </div>
          )}
        </Card>
      )}

      <Card title="Tools">
        <div className="flex flex-wrap gap-2">
          <ActionButton id="time.burst" label="Poll all sources now" onDone={onChanged} />
          <ActionButton id="time.restart_chrony" label="Restart chrony" onDone={onChanged} />
          <ActionButton id="time.restart_timed" label="Restart dawn-timed" onDone={onChanged} />
        </div>
        <p className="text-xs text-muted mt-2">Alarms run from the system clock, so a short gap in a time source does not affect them.</p>
      </Card>

      <Card title="History"><HistorySection specs={TIME_HISTORY} hours={hours} onHours={onHours} tz={tz} h24={h24} area="time" /></Card>
    </div>
  );
}

const OPT: Record<string, string> = { P: 'prefer', N: 'noselect', T: 'trust', R: 'require' };
const options = (eff: string) => eff.split('').map((c) => OPT[c] ?? c).join(', ') || '–';
function ntpLine(s: ChronySource) {
  const n = s.ntp;
  if (!n) return '';
  return ` · stratum ${n.stratum}, round trip ${sec(n.peer_delay_s)}, polled every ${fmtNum(n.poll_s, 0)} s, ${n.total_valid_rx}/${n.total_tx} replies valid`;
}
