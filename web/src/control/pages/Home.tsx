import { useState } from '../../shared/store';
import { Card, Dot } from '../../shared/components';
import { fmtDate, fmtTime, useNow, fmtDayTime, fmtIn } from '../../shared/time';

export function Home() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-end justify-between">
          <div>
            <div className="text-5xl font-semibold tnum tracking-tight">{t.hm}{t.ampm && <span className="text-2xl text-muted ml-1">{t.ampm}</span>}</div>
            <div className="text-muted mt-1">{fmtDate(now, s.tz)}</div>
          </div>
          <div className="flex gap-3 text-xs text-muted">
            <span className="flex items-center gap-1"><Dot state={s.time_sources.gps.fix >= 2 ? 'ok' : 'off'} />GPS</span>
            <span className="flex items-center gap-1"><Dot state={s.dab.sync ? 'ok' : 'off'} />DAB</span>
            <span className="flex items-center gap-1"><Dot state={s.system.network.online ? 'ok' : 'off'} />Net</span>
          </div>
        </div>
      </Card>
      <Card title="Next alarm">
        {s.alarms.next ? (
          <div className="flex items-center justify-between">
            <div><div className="text-lg">{s.alarms.next.label}</div><div className="text-muted text-sm">{fmtDayTime(s.alarms.next.at, s.tz, s.settings.clock_24h)}</div></div>
            <div className="text-muted text-sm">in {fmtIn(s.alarms.next.in_seconds)}</div>
          </div>
        ) : <div className="text-muted">No alarm scheduled</div>}
      </Card>
    </div>
  );
}
