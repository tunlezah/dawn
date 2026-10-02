import { useState } from '../../shared/store';
import { fmtDuration, useNow } from '../../shared/time';
import { channelFrequency } from '../../shared/dab';
import { FaceHeader, SourceIcon, SourceStatus } from '../components/Header';
import { ControlBar } from '../components/ControlBar';
import { IconRadio } from '../components/icons';

function Art({ src, className = '' }: { src: string | null; className?: string }) {
  if (src) return <img className={`f-art logo-tile ${className}`} src={src} alt="" />;
  return <div className={`f-art placeholder ${className}`}><IconRadio /></div>;
}

/** Long one-line text scrolls (marquee) unless low-CPU; shorter text wraps to two lines. */
function Scrolling({ text, className, lowCpu }: { text: string; className: string; lowCpu: boolean }) {
  if (text.length > 60 && !lowCpu) {
    return <div className={`${className} f-marquee-wrap`}><span className="marquee">{text}<span className="inline-block w-[12vmin]" />{text}<span className="inline-block w-[12vmin]" /></span></div>;
  }
  return <div className={`${className} truncate-2`}>{text}</div>;
}

export function Playing() {
  const s = useState();
  const now = useNow();
  const np = s.now_playing;
  const isRadio = np.source === 'dab' || np.source === 'url' || np.source === 'playlist' || np.source === 'chime';

  let body;
  if (isRadio) {
    const freq = channelFrequency(s.dab.channel);
    const meta = [np.source === 'dab' ? s.dab.services.find((x) => x.sid === np.station_sid)?.pty : null, np.codec, np.bitrate ? `${np.bitrate} kbps` : null].filter(Boolean) as string[];
    const tech = np.source === 'dab' ? ['DAB', s.dab.channel, freq, s.dab.ensemble].filter(Boolean) as string[] : [np.url ?? ''].filter(Boolean);
    body = (
      <div className="f-body">
        <div className="f-info">
          <div className="f-title xl truncate">{np.station || np.title || 'Radio'}</div>
          {np.source === 'dab' && s.dab.ensemble && <div className="f-eyebrow truncate">{s.dab.ensemble}</div>}
          {meta.length > 0 && <div className="f-meta truncate">{meta.map((m, i) => <span key={i}>{i > 0 && <span className="f-sep" />}{m}</span>)}</div>}
          <hr className="f-hr" style={{ width: '30vmin', margin: '1.2vmin 0' }} />
          {(np.dls || np.title) && <Scrolling text={np.dls || np.title || ''} className="f-dls" lowCpu={s.display.low_cpu} />}
          {tech.length > 0 && <div className="f-tiny truncate tnum">{tech.map((m, i) => <span key={i}>{i > 0 && <span className="f-sep" />}{m}</span>)}</div>}
        </div>
        <Art src={np.slide_url || np.logo_url} />
      </div>
    );
  } else {
    // AirPlay / Bluetooth: artwork left, track identity right, progress when the sender reports it.
    let pos = np.position_s, dur = np.duration_s;
    if (pos !== null && np.position_at) pos += Math.max(0, (now.getTime() - new Date(np.position_at).getTime()) / 1000);
    if (pos !== null && dur) pos = Math.min(pos, dur);
    const paused = np.source === 'airplay' ? !s.airplay.playing : np.source === 'bluetooth' ? !s.bluetooth.playing : false;
    body = (
      <div className="f-body">
        <Art src={np.artwork_url || np.logo_url} />
        <div className="f-info">
          <div className="f-title truncate">{np.title || 'Playing'}</div>
          {np.artist && <div className="f-sub truncate">{np.artist}</div>}
          {np.station && <div className="f-device truncate"><SourceIcon source={np.source} /><span className="truncate">{np.station}</span></div>}
          {pos !== null && dur ? (
            <>
              <div className={`f-progress ${paused ? 'paused' : ''}`}><div style={{ width: `${Math.min(100, (pos / dur) * 100)}%` }} /></div>
              <div className="f-times f-tiny tnum"><span>{fmtDuration(pos)}</span><span>{paused ? 'paused' : ''}</span><span>{fmtDuration(dur)}</span></div>
            </>
          ) : paused ? <div className="f-tiny mt-[2vmin]">Paused</div> : null}
        </div>
      </div>
    );
  }

  return (
    <div className="face-screen fade-in">
      <FaceHeader left={<><SourceStatus />{s.timers.sleep && <span className="face-chip quiet ml-[2vmin]">sleep · {Math.ceil(s.timers.sleep.remaining_s / 60)} min</span>}</>} />
      {body}
      <ControlBar />
    </div>
  );
}
