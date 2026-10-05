import { useEffect, useState } from 'react';
import { Card } from '../../shared/components';
import { DAB_CHANNELS } from '../../shared/dab';
import { usePoll, type DabFacts, type Live, type Plot } from './api';
import { ColumnChart, Figure, fmtNum, fmtU, LineChart, Meter, Sparkline, Stat, Table, xyTable, VIZ, type Tone } from './charts';
import { ActionButton, ago, CheckList, Facts, HistorySection, sinceIso, StatusWord, type ChartSpec } from './common';

// welle's SNR estimate: same thresholds as the checks (core/dawn_core/diagnostics/dab.py)
const SNR_GOOD = 12, SNR_POOR = 8;
// R82xx tuner gain steps (dB); welle-cli -g takes the index
const GAINS = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6];
const GUARD_US = 246; // DAB mode I guard interval

const snrTone = (snr: number | null, sync: boolean): Tone => (!sync || snr === null ? 'off' : snr >= SNR_GOOD ? 'ok' : snr >= SNR_POOR ? 'warn' : 'err');
const toneStatus = (t: Tone) => (t === 'ok' ? 'ok' : t === 'warn' ? 'warn' : t === 'err' ? 'fail' : 'off') as 'ok' | 'warn' | 'fail' | 'off';

export const DAB_HISTORY: ChartSpec[] = [
  { title: 'Signal-to-noise ratio', metrics: [{ key: 'dab.snr' }], unit: 'dB', refs: [{ y: SNR_GOOD, label: `${SNR_GOOD} dB solid` }, { y: SNR_POOR, label: `${SNR_POOR} dB breaks up` }], digits: 1, sub: 'welle-cli’s estimate for the tuned ensemble' },
  { title: 'Ensemble decoded', metrics: [{ key: 'dab.sync' }], unit: '%', yMin: 0, yMax: 100, digits: 0, sub: 'share of samples with sync' },
  { title: 'Data-channel (FIC) errors', metrics: [{ key: 'dab.fic' }], unit: '/min', yMin: 0, digits: 1, sub: 'CRC failures in the service information; 0 is normal' },
  { title: 'Audio decode errors', metrics: [{ key: 'dab.err' }], unit: '/min', yMin: 0, digits: 1, sub: 'frame + Reed-Solomon + AAC errors on the station playing' },
  { title: 'Frequency correction', metrics: [{ key: 'dab.freq' }], unit: 'Hz', digits: 0, sub: 'how far the stick’s crystal is off; steady is fine, jumps are not' },
  { title: 'Tuner gain', metrics: [{ key: 'dab.gain' }], unit: 'dB', digits: 1 },
];

export function DabPanel({ tz, h24, hours, onHours }: { tz: string; h24: boolean; hours: number; onHours: (h: number) => void }) {
  const live = usePoll<Live<DabFacts>>('/api/diag/live/dab', 1000);
  const f = live.data?.facts;
  const m = f?.mux ?? null;
  const [snrs, setSnrs] = useState<number[]>([]);  // the last two minutes, for the sparkline
  useEffect(() => {
    const snr = live.data?.facts.mux?.snr;
    if (live.data?.facts.mux?.sync && snr !== null && snr !== undefined) setSnrs((a) => [...a.slice(-119), snr]);
  }, [live.data]);
  const tone = snrTone(m?.snr ?? null, m?.sync ?? false);

  return (
    <div className="space-y-4">
      <Card title="Live signal" action={<span className="text-xs text-muted">{live.error ? `⚠ ${live.error}` : 'updates every second'}</span>}>
        {!f ? <div className="text-sm text-muted">Reading the decoder…</div> : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              <Stat label="Signal-to-noise" value={m?.sync && m.snr !== null ? fmtU(m.snr, 'dB', 1) : 'no sync'} sub={<StatusWord status={toneStatus(tone)} />}>
                <Meter value={m?.sync ? m.snr : null} min={0} max={25} tone={tone} label="SNR from 0 to 25 dB" />
                <div className="mt-2"><Sparkline values={snrs} label="SNR over the last two minutes" /></div>
              </Stat>
              <Stat label="Ensemble" value={m?.ensemble ?? '–'} sub={m?.channel ? `${m.channel}${m.mhz ? ` · ${m.mhz.toFixed(3)} MHz` : ''}` : 'not tuned'} />
              <Stat label="Data-channel errors" value={m?.fic_errors_per_min !== null && m?.fic_errors_per_min !== undefined ? fmtU(m.fic_errors_per_min, '/min', 1) : '–'} sub={`${fmtNum(m?.fic_crc_errors ?? null, 0)} CRC failures since the decoder started`} />
              <Stat label="Frequency correction" value={fmtU(m?.freq_correction_hz ?? null, 'Hz', 0)} sub="the stick’s crystal offset" />
              <Stat label="Tuner gain" value={m?.gain_db !== null && m?.gain_db !== undefined ? fmtU(m.gain_db, 'dB', 1) : '–'} sub={f.gain_config === null ? 'automatic (AGC)' : `fixed at ${f.gain_config} dB`} />
              <Stat label="Last time signal" value={m?.fct0_age_s !== null && m?.fct0_age_s !== undefined ? ago(m.fct0_age_s) : '–'} sub="frame counter; it ticks every ~12 s when the ensemble decodes" />
            </div>
            {f.playing && <div className="text-sm text-muted mt-3">Playing <b className="text-fg">{f.playing.label}</b>.</div>}
          </>
        )}
      </Card>

      <Card title="Checks"><CheckList checks={live.data?.checks ?? []} onChanged={live.reload} /></Card>

      <Plots channel={m?.channel ?? null} centre={m?.mhz ?? null} sync={m?.sync ?? false} />

      {f && f.services.length > 0 && (
        <Card title="Stations in this ensemble">
          <div className="overflow-x-auto">
            <Table head={['Station', 'SId', 'Codec', 'kbit/s', 'Protection', 'Decoding', 'Errors /min (frame · RS · AAC)']} rows={f.services.map((sv) => [
              sv.label, sv.sid, sv.codec ?? '–', sv.bitrate ?? '–', sv.protection ?? '–',
              sv.decoding ? `yes${sv.audio_format ? ` · ${sv.audio_format}` : ''}` : 'no',
              sv.rates ? `${fmtNum(sv.rates.frame, 1)} · ${fmtNum(sv.rates.rs, 1)} · ${fmtNum(sv.rates.aac, 1)}` : sv.decoding ? `${sv.frame_errors} · ${sv.rs_errors} · ${sv.aac_errors} total` : '–',
            ])} />
          </div>
          <p className="text-xs text-muted mt-2">welle-cli decodes a station when something listens to it, so only the station playing shows live error rates.</p>
        </Card>
      )}

      {m && m.tii.length > 0 && (
        <Card title="Transmitters heard (TII)">
          <Table head={['Transmitter id (comb / pattern)', 'Extra path', 'Fit error']} rows={m.tii.map((t) => [`${t.comb} / ${t.pattern}`, `${fmtNum(t.delay_km, 1)} km`, fmtNum(t.error, 3)])} />
          <p className="text-xs text-muted mt-2">Each row is a transmitter of this ensemble identified by its TII code. “Extra path” is how much further its signal travels than the first one received.</p>
        </Card>
      )}

      {f && <Tools f={f} onDone={live.reload} />}

      {f && <Receiver f={f} />}

      <Card title="History"><HistorySection specs={DAB_HISTORY} hours={hours} onHours={onHours} tz={tz} h24={h24} area="dab" /></Card>
    </div>
  );
}

function Plots({ channel, centre, sync }: { channel: string | null; centre: number | null; sync: boolean }) {
  const [paused, setPaused] = useState(false);
  const every = paused ? 0 : 3000;
  const spec = usePoll<Plot>('/api/diag/dab/plot/spectrum', every);
  const nul = usePoll<Plot>('/api/diag/dab/plot/nullspectrum', every);
  const cir = usePoll<Plot>('/api/diag/dab/plot/impulseresponse', every);
  const con = usePoll<Plot>('/api/diag/dab/plot/constellation', every);
  const freqs = (p: Plot | null) => {
    const n = p?.bins?.length ?? 0, span = p?.span_mhz ?? 2.048, c = centre ?? 0;
    return Array.from({ length: n }, (_, i) => c - span / 2 + (i + 0.5) * (span / n));
  };
  const unit = centre ? 'MHz' : 'MHz from centre';
  const cirX = (p: Plot | null) => {
    const n = p?.bins?.length ?? 0, total = p?.n ?? n, us = p?.us_per_sample ?? 0.4883, peak = p?.peak_index ?? 0;
    return Array.from({ length: n }, (_, i) => ((i + 0.5) * (total / Math.max(1, n)) - peak) * us);
  };
  const none = <div className="text-sm text-muted py-6 text-center">{channel ? 'welle-cli did not return this plot.' : 'Not tuned.'}</div>;
  return (
    <Card title="Signal plots" action={<button type="button" className="btn btn-sm" onClick={() => setPaused(!paused)}>{paused ? 'Resume' : 'Pause'}</button>}>
      <div className="space-y-5">
        <Figure title="Spectrum" sub="A good ensemble is a flat-topped block about 1.5 MHz wide standing well clear of the floor; a slope or notch means multipath or a weak signal." busy={spec.busy && !paused}
          table={() => spec.data?.bins ? xyTable(freqs(spec.data), spec.data.bins, unit, 'dB', 3, 1) : none}>
          {spec.data?.available && spec.data.bins
            ? <LineChart xs={freqs(spec.data)} ys={spec.data.bins} xUnit={unit} yUnit="dB" xTitle={centre ? 'Frequency (MHz)' : 'Frequency (MHz from the channel centre)'} xDigits={2} label="Spectrum of the tuned channel"
              refs={spec.data.floor_db !== undefined ? [{ y: spec.data.floor_db, label: `floor ${fmtNum(spec.data.floor_db, 1)} dB` }] : []} />
            : none}
        </Figure>
        <Figure title="Interference (null symbol)" sub="Measured while the transmitter is silent between frames: anything standing up here is interference, not DAB." busy={nul.busy && !paused}
          table={() => nul.data?.bins ? xyTable(freqs(nul.data), nul.data.bins, unit, 'dB', 3, 1) : none}>
          {nul.data?.available && nul.data.bins ? <LineChart xs={freqs(nul.data)} ys={nul.data.bins} xUnit={unit} yUnit="dB" xTitle={centre ? 'Frequency (MHz)' : 'Frequency (MHz from the channel centre)'} xDigits={2} color={VIZ.dim} label="Spectrum during the null symbol" /> : none}
        </Figure>
        <Figure title="Impulse response" sub={`Each peak is a path from a transmitter (or a reflection) to the antenna. Paths more than ${GUARD_US} µs after the first fall outside the guard interval and cause errors.`}
          busy={cir.busy && !paused} table={() => cir.data?.bins ? xyTable(cirX(cir.data), cir.data.bins, 'µs after the strongest path', 'dB', 1, 1) : none}>
          {cir.data?.available && cir.data.bins
            ? <LineChart xs={cirX(cir.data)} ys={cir.data.bins} xUnit="µs" yUnit="dB" xTitle="Delay after the strongest path (µs)" xDigits={0} label="Channel impulse response"
              dots={[{ x: 0, y: Math.max(...cir.data.bins), label: 'strongest path' }]} />
            : none}
        </Figure>
        <Figure title="Constellation (symbol phases)" sub={con.data?.phase_error_deg !== undefined && con.data?.phase_error_deg !== null
          ? `RMS phase error ${fmtNum(con.data.phase_error_deg, 1)}°. Clean reception shows four narrow peaks at −135°, −45°, 45° and 135°; smeared peaks mean noise or multipath.`
          : 'Clean reception shows four narrow peaks at −135°, −45°, 45° and 135°.'} busy={con.busy && !paused}
          table={() => con.data?.histogram ? <Table head={['Phase', 'Symbols']} rows={con.data.histogram.map((v, i) => [`${-180 + i * 5}° to ${-175 + i * 5}°`, v])} /> : none}>
          {con.data?.available && con.data.histogram
            ? <ColumnChart unit="symbols" label="Histogram of DQPSK symbol phases" height={144} xTitle="Symbol phase (5° steps)"
              cols={con.data.histogram.map((v, i) => ({ key: String(i), label: `${-180 + i * 5}° to ${-175 + i * 5}°`, value: v, color: VIZ.s1 }))}
              xLabel={(_, i) => ([9, 27, 45, 63].includes(i) ? `${-180 + i * 5}°` : null)} />
            : none}
        </Figure>
        {!sync && channel && <p className="text-xs text-warn">The decoder has no sync on {channel}: these plots show what the antenna receives, but there is no ensemble to decode.</p>}
      </div>
    </Card>
  );
}

function Tools({ f, onDone }: { f: DabFacts; onDone: () => void }) {
  const known = f.ensembles.map((e) => e.channel);
  const [channel, setChannel] = useState(f.mux?.channel ?? known[0] ?? '9A');
  const [gain, setGain] = useState(f.gain_config === null ? 'auto' : String(f.gain_config));
  useEffect(() => { if (f.mux?.channel) setChannel(f.mux.channel); }, [f.mux?.channel]);
  useEffect(() => { setGain(f.gain_config === null ? 'auto' : String(GAINS.reduce((a, b) => (Math.abs(b - (f.gain_config ?? 0)) < Math.abs(a - (f.gain_config ?? 0)) ? b : a)))); }, [f.gain_config]);
  return (
    <Card title="Tools">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-sm text-muted w-28" htmlFor="dab-ch">Tune to</label>
          <select id="dab-ch" className="!w-60" value={channel} onChange={(e) => setChannel(e.target.value)}>
            {known.length > 0 && <optgroup label="Ensembles found">{f.ensembles.map((e) => <option key={`k${e.channel}`} value={e.channel}>{e.channel} · {e.label}</option>)}</optgroup>}
            <optgroup label="All Band III">{DAB_CHANNELS.map((c) => <option key={c} value={c}>{c}</option>)}</optgroup>
          </select>
          <ActionButton id="dab.retune" label="Tune" value={channel} onDone={onDone} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-sm text-muted w-28" htmlFor="dab-gain">Tuner gain</label>
          <select id="dab-gain" className="!w-60" value={gain} onChange={(e) => setGain(e.target.value)}>
            <option value="auto">Automatic (AGC)</option>
            {GAINS.map((g) => <option key={g} value={String(g)}>{g.toFixed(1)} dB</option>)}
          </select>
          <ActionButton id="dab.gain" label="Apply" value={gain === 'auto' ? 'auto' : Number(gain)} onDone={onDone} />
        </div>
        <p className="text-xs text-muted">With a strong local transmitter, a fixed gain a few steps below the AGC’s choice can stop overload; with a weak one, a higher fixed gain can help. Applying restarts the decoder (a few seconds of silence) and is saved as <code>dab.gain</code>.</p>
        <div className="flex flex-wrap gap-2 pt-1">
          <ActionButton id="dab.scan" label="Scan for stations" onDone={onDone} confirm="Scanning retunes the radio for about a minute; anything playing on DAB stops. Scan now?" />
          <ActionButton id="dab.restart" label="Restart the decoder" onDone={onDone} />
        </div>
      </div>
    </Card>
  );
}

function Receiver({ f }: { f: DabFacts }) {
  const m = f.mux;
  return (
    <Card title="Receiver and decoder">
      <Facts rows={[
        ['SDR stick', f.sdr.present ? `${f.sdr.tuner ?? 'RTL2832U'}${f.sdr.usb[0] ? ` · USB ${f.sdr.usb[0].vid}:${f.sdr.usb[0].pid} ${f.sdr.usb[0].product ?? ''}${f.sdr.usb[0].power ? ` · draws up to ${f.sdr.usb[0].power}` : ''}` : ''}` : 'not detected'],
        ['DVB-T kernel driver', f.dvb_driver_loaded ? 'loaded (it holds the stick: blacklist dvb_usb_rtl28xxu)' : 'not loaded (good)'],
        ['Decoder service', `${f.service_name}: ${f.unit ?? 'unknown'}`],
        ['welle-cli web server', `${f.welle_url} · ${f.reachable ? 'answering' : 'not answering'}`],
        ['Command line', f.cmdline ? <code className="text-xs break-all">{f.cmdline}</code> : '–'],
        ['Expected arguments', <code className="text-xs">{f.expected_args.join(' ') || '(none)'}</code>],
        ...(f.arg_notes.length ? [['Argument notes', f.arg_notes.join(' · ')] as [string, string]] : []),
        ['Software', m?.software ?? '–'],
        ['Tuner reported', m?.hardware ?? '–'],
        ['Stations known', `${f.stations} from ${f.ensembles.length} ensemble(s) · last scan ${sinceIso(f.last_scan_at)}${f.scanning ? ' · scanning now' : ''}`],
        ['Restarts by an alarm (24 h)', f.restarts_24h.length ? `${f.restarts_24h.length}, last ${sinceIso(f.restarts_24h[0])}` : 'none'],
        ['Alarm fallbacks (24 h)', f.fallbacks_24h.length ? `${f.fallbacks_24h.length}: an alarm played the chime because DAB gave no audio` : 'none'],
        ...(f.presets_unknown.length ? [['Presets not found', f.presets_unknown.join(', ')] as [string, string]] : []),
        ...(f.alarms_unknown.length ? [['Alarms with unknown station', f.alarms_unknown.join(', ')] as [string, string]] : []),
      ]} />
      {f.ensembles.length > 0 && (
        <div className="mt-3">
          <div className="text-sm font-medium mb-1">Ensembles found by the last scan</div>
          <Table head={['Channel', 'Ensemble', 'SNR at scan']} rows={f.ensembles.map((e) => [e.channel, e.label, e.snr !== null ? fmtU(e.snr, 'dB', 1) : '–'])} />
        </div>
      )}
      <div className="mt-3">
        <div className="text-sm font-medium mb-1">Decoder messages</div>
        <pre className="text-[11px] leading-snug bg-black/30 rounded-xl p-3 max-h-48 overflow-auto whitespace-pre-wrap break-all">{f.messages.length ? f.messages.map((x) => x.text).join('\n') : 'No messages from welle-cli yet.'}</pre>
      </div>
    </Card>
  );
}
