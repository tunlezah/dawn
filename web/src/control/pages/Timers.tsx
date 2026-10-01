import { useState } from '../../shared/store';
import { Card } from '../../shared/components';
import { actions } from '../../shared/api';
import { fmtDuration, useNow } from '../../shared/time';

function Countdown({ ends_at, total_s }: { ends_at: string; total_s: number }) {
  const now = useNow();
  const remaining = Math.max(0, (new Date(ends_at).getTime() - now.getTime()) / 1000);
  const pct = total_s ? Math.min(100, (remaining / total_s) * 100) : 0;
  return (
    <div>
      <div className="text-5xl font-semibold tnum">{fmtDuration(remaining)}</div>
      <div className="h-1.5 rounded-full bg-elev-2 overflow-hidden mt-3"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export function Timers() {
  const s = useState();
  const playing = s.audio.active_source !== 'none';
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Timers</h1>
      <Card title="Sleep timer" action={s.timers.sleep && <button className="btn btn-sm" onClick={() => actions.cancelSleep()}>Cancel</button>}>
        {s.timers.sleep ? (
          <div>
            <Countdown ends_at={s.timers.sleep.ends_at} total_s={s.timers.sleep.total_s} />
            <p className="text-sm text-muted mt-3">{s.timers.sleep.fading ? 'Fading out…' : `Playing ${s.now_playing.station || s.now_playing.title || ''}, then a 30 s fade to standby.`}</p>
          </div>
        ) : (
          <div>
            <p className="text-sm text-muted mb-3">{playing ? 'Keep playing for…' : 'Starts the last played source, then fades to standby after…'}</p>
            <div className="grid grid-cols-5 gap-2">{s.timers.sleep_choices.map((m) => <button key={m} className="btn" onClick={() => actions.sleep(m)}><span className="tnum">{m}</span>&nbsp;min</button>)}</div>
          </div>
        )}
      </Card>
      <Card title="Nap timer" action={s.timers.nap && <button className="btn btn-sm" onClick={() => actions.cancelNap()}>Cancel</button>}>
        {s.timers.nap ? (
          <div>
            <Countdown ends_at={s.timers.nap.ends_at} total_s={s.timers.nap.total_s} />
            <p className="text-sm text-muted mt-3">Rings the chime when done. Independent of your alarms.</p>
          </div>
        ) : (
          <div>
            <p className="text-sm text-muted mb-3">One tap. Also from the face menu or by holding the encoder.</p>
            <div className="grid grid-cols-4 gap-2">{s.timers.nap_choices.map((m) => <button key={m} className="btn" onClick={() => actions.nap(m)}><span className="tnum">{m}</span>&nbsp;min</button>)}</div>
          </div>
        )}
      </Card>
    </div>
  );
}
