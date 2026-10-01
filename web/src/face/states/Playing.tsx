import { useState } from '../../shared/store';
import { fmtTime, useNow } from '../../shared/time';

export function Playing() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const np = s.now_playing;
  const art = np.artwork_url || np.slide_url || np.logo_url;
  const line1 = np.title || np.station || 'Playing';
  const line2 = np.artist || np.dls || (np.source === 'dab' ? np.station : null);
  return (
    <div className="face-screen fade-in">
      <div className="flex items-center justify-between">
        <div className="face-dots"><span className={`dot ${s.audio.audio_flowing ? 'dot-ok' : 'dot-warn'}`} />{np.source.toUpperCase()}{np.signal !== null && <span className="tnum">· {np.signal}%</span>}</div>
        <div className="face-time small tnum">{t.hm}{t.ampm && <span className="text-[4vmin] text-muted ml-[1vmin]">{t.ampm}</span>}</div>
      </div>
      <div className="flex-1 flex items-center gap-[6vmin] min-h-0">
        {art ? <img className="logo-tile" src={art} alt="" /> : <div className="logo-tile" />}
        <div className="min-w-0 flex-1">
          <div className="text-[7.5vmin] font-semibold leading-tight truncate">{line1}</div>
          {line2 && <div className="text-[4.6vmin] text-muted leading-snug mt-[1.5vmin] line-clamp-2">{line2}</div>}
          {np.title && np.station && <div className="text-[3.6vmin] text-faint mt-[1.5vmin] truncate">{np.station}</div>}
        </div>
      </div>
      <div className="flex items-end justify-between">
        <div className="face-dots">{s.timers.sleep && <span className="face-chip">sleep · {Math.ceil(s.timers.sleep.remaining_s / 60)} min</span>}</div>
        <div className="face-chip tnum">vol {s.audio.volume}</div>
      </div>
    </div>
  );
}
