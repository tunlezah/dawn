import { useEffect, useState as useReactState } from 'react';
import { useState } from '../../shared/store';
import { Card, Dot, Slider } from '../../shared/components';
import { fmtDate, fmtTime, useNow, fmtDayTime, fmtIn, fmtDuration } from '../../shared/time';
import { actions } from '../../shared/api';
import { navigate } from '../../shared/router';
import { prepNote, ringNote } from '../../shared/alarms';

export function Home() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const [vol, setVol] = useReactState(s.audio.volume);
  useEffect(() => setVol(s.audio.volume), [s.audio.volume]);
  const np = s.now_playing;
  const playing = s.audio.active_source !== 'none';
  const art = np.artwork_url || np.slide_url || np.logo_url;

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-end justify-between">
          <div>
            <div className="text-5xl font-semibold tnum tracking-tight">{t.hm}{t.ampm && <span className="text-xl text-muted ml-2 tracking-wider uppercase">{t.ampm}</span>}</div>
            <div className="text-muted mt-1">{fmtDate(now, s.tz)}</div>
          </div>
          <div className="flex gap-3 text-xs text-muted">
            <span className="flex items-center gap-1"><Dot state={s.time_sources.gps.fix >= 2 ? 'ok' : 'off'} />GPS</span>
            <span className="flex items-center gap-1"><Dot state={s.dab.sync ? 'ok' : 'off'} />DAB</span>
            <span className="flex items-center gap-1"><Dot state={s.system.network.online ? 'ok' : 'off'} />Net</span>
          </div>
        </div>
      </Card>

      {s.diagnostics.problems.length > 0 && (
        <Card className={s.diagnostics.fail ? 'border-err' : ''}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="font-medium">{s.diagnostics.fail ? `${s.diagnostics.fail} problem${s.diagnostics.fail > 1 ? 's' : ''}` : `${s.diagnostics.warn} warning${s.diagnostics.warn > 1 ? 's' : ''}`} found</div>
              <ul className="text-sm text-muted mt-1 space-y-0.5">
                {s.diagnostics.problems.slice(0, 3).map((p) => (
                  <li key={p.id} className="truncate"><span className={p.status === 'fail' ? 'text-err' : 'text-warn'} aria-label={p.status === 'fail' ? 'problem' : 'warning'}>{p.status === 'fail' ? '✕' : '!'}</span> {p.title}: {p.detail}</li>
                ))}
              </ul>
            </div>
            <button className="btn btn-sm shrink-0" onClick={() => navigate('/diagnostics')}>Diagnostics</button>
          </div>
        </Card>
      )}

      {s.alarms.ringing && (
        <Card className="border-accent">
          <div className="flex items-center justify-between gap-3">
            <div><div className="text-accent font-semibold">{s.alarms.ringing.snoozed_until ? 'Snoozed' : 'Ringing'} · {s.alarms.ringing.label}</div><div className="text-sm text-muted">{ringNote(s.alarms.ringing)}</div></div>
            <div className="flex gap-2">
              {!s.alarms.ringing.snoozed_until && <button className="btn" onClick={() => actions.snooze()}>Snooze</button>}
              <button className="btn btn-danger" onClick={() => actions.stopRinging()}>Stop</button>
            </div>
          </div>
        </Card>
      )}

      <Card title="Now playing" action={playing ? <button className="btn btn-sm" onClick={() => actions.standby()}>Stop</button> : undefined}>
        {playing ? (
          <div className="flex items-center gap-4">
            {art ? <img src={art} alt="" className="w-16 h-16 rounded-xl object-cover bg-elev-2" /> : <div className="w-16 h-16 rounded-xl bg-elev-2 flex items-center justify-center text-2xl">📻</div>}
            <div className="min-w-0">
              <div className="font-medium truncate">{np.title || np.station || 'Playing'}</div>
              <div className="text-sm text-muted truncate">{np.artist || np.dls || np.station || np.source}</div>
              <div className="text-xs text-faint mt-0.5 flex items-center gap-2"><Dot state={s.audio.audio_flowing ? 'ok' : 'warn'} />{s.audio.audio_flowing ? 'audio flowing' : 'waiting for audio'} · {np.source}</div>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <div className="text-muted">Standby</div>
            <div className="flex gap-2 flex-wrap justify-end">
              {s.presets.slice(0, 3).map((p) => <button key={p.id} className="btn btn-sm" onClick={() => actions.play(p.source)}>{p.label}</button>)}
              <button className="btn btn-sm" onClick={() => navigate('/radio')}>Radio…</button>
            </div>
          </div>
        )}
        <div className="mt-4 flex items-center gap-3">
          <button className="btn btn-sm" aria-label={s.audio.muted ? 'Unmute' : 'Mute'} onClick={() => actions.mute()}>{s.audio.muted ? '🔇' : '🔊'}</button>
          <Slider label="Volume" value={vol} onChange={setVol} onCommit={(v) => actions.setVolume(v)} />
          <span className="tnum w-8 text-right text-sm text-muted">{s.audio.muted ? '—' : vol}</span>
        </div>
      </Card>

      <Card title="Next alarm" action={<button className="btn btn-sm" onClick={() => navigate('/alarms')}>Alarms</button>}>
        {s.alarms.next ? (
          <>
            <div className="flex items-center justify-between">
              <div><div className="text-lg">{s.alarms.next.label}</div><div className="text-muted text-sm">{fmtDayTime(s.alarms.next.at, s.tz, s.settings.clock_24h)}</div></div>
              <div className="text-muted text-sm">in {fmtIn(s.alarms.next.in_seconds)}</div>
            </div>
            {(() => {
              const p = prepNote(s.alarms.prepare, s.alarms.next?.id);
              return p && <div className={`text-xs mt-2 flex items-center gap-2 ${p.ok ? 'text-muted' : 'text-warn'}`}><Dot state={p.ok ? 'ok' : 'warn'} />{p.text}</div>;
            })()}
          </>
        ) : <div className="text-muted">{s.alarms.on_leave_until ? `On leave until ${s.alarms.on_leave_until}` : 'No alarm scheduled'}</div>}
      </Card>

      <Card title="Quick actions">
        <div className="grid grid-cols-3 gap-2">
          {s.timers.nap ? (
            <button className="btn" onClick={() => actions.cancelNap()}>Nap {fmtDuration(Math.max(0, (new Date(s.timers.nap.ends_at).getTime() - now.getTime()) / 1000))} ✕</button>
          ) : (
            <button className="btn" onClick={() => actions.nap(s.timers.nap_choices[0] ?? 20)}>Nap {s.timers.nap_choices[0] ?? 20} min</button>
          )}
          {s.timers.sleep ? (
            <button className="btn" onClick={() => actions.cancelSleep()}>Sleep {fmtDuration(Math.max(0, (new Date(s.timers.sleep.ends_at).getTime() - now.getTime()) / 1000))} ✕</button>
          ) : (
            <button className="btn" onClick={() => actions.sleep(s.timers.sleep_choices[1] ?? 30)} disabled={!playing}>Sleep {s.timers.sleep_choices[1] ?? 30} min</button>
          )}
          <button className="btn" onClick={() => actions.mute()}>{s.audio.muted ? 'Unmute' : 'Mute'}</button>
        </div>
      </Card>
    </div>
  );
}
