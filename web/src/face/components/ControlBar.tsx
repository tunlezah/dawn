import type { PointerEvent, ReactNode } from 'react';
import { actions } from '../../shared/api';
import { useState } from '../../shared/store';
import { IconMinus, IconMuted, IconNext, IconPause, IconPlay, IconPlus, IconPrev, IconStar, IconVolume } from './icons';

const stop = (e: PointerEvent) => e.stopPropagation();
const run = (p: Promise<unknown>) => p.catch(() => {});

/** Volume cell: − / mute toggle / +. Shared by the control bar and the ambient strip. */
export function VolumeCell({ readOnly = false }: { readOnly?: boolean }) {
  const s = useState();
  const v = s.audio.volume;
  const icon = s.audio.muted ? <IconMuted /> : <IconVolume level={v === 0 ? 0 : v < 50 ? 1 : 2} />;
  if (readOnly) return <div className="cell"><div className="vol">{icon}<span>{s.audio.muted ? '—' : v}</span></div></div>;
  return (
    <>
      <button className="narrow" aria-label="Volume down" onPointerDown={stop} onClick={() => run(actions.volumeStep(-1))}><IconMinus /></button>
      <button aria-label={s.audio.muted ? 'Unmute' : 'Mute'} onPointerDown={stop} onClick={() => run(actions.mute())}><div className="vol">{icon}<span>{s.audio.muted ? '—' : v}</span></div></button>
      <button className="narrow" aria-label="Volume up" onPointerDown={stop} onClick={() => run(actions.volumeStep(1))}><IconPlus /></button>
    </>
  );
}

/** Persistent bottom control bar while playing. Transport for phones, preset/star for radio; volume on the right. */
export function ControlBar() {
  const s = useState();
  const np = s.now_playing;
  let transport: ReactNode;
  if (np.source === 'airplay' || np.source === 'bluetooth') {
    const bt = np.source === 'bluetooth';
    const playing = bt ? s.bluetooth.playing : s.airplay.playing;
    const send = (c: 'Previous' | 'Next' | 'PlayPause') => run(bt ? actions.bluetoothPlayer(c === 'PlayPause' ? (playing ? 'Pause' : 'Play') : c) : actions.airplayRemote(c));
    transport = (
      <>
        <button aria-label="Previous track" onPointerDown={stop} onClick={() => send('Previous')}><IconPrev /></button>
        <button className="primary" aria-label={playing ? 'Pause' : 'Play'} onPointerDown={stop} onClick={() => send('PlayPause')}>{playing ? <IconPause /> : <IconPlay />}</button>
        <button aria-label="Next track" onPointerDown={stop} onClick={() => send('Next')}><IconNext /></button>
      </>
    );
  } else {
    const source = np.source === 'dab' && np.station_sid ? `dab:${np.station_sid}` : np.url;
    const preset = source ? s.presets.find((p) => p.source === source) : undefined;
    const toggleStar = () => {
      if (!source) return;
      run(preset ? actions.deletePreset(preset.id) : actions.addPreset(np.station || np.title || source, source));
    };
    transport = (
      <>
        <button aria-label="Previous preset" onPointerDown={stop} onClick={() => run(actions.prevPreset())}><IconPrev /></button>
        <button className={`primary ${preset ? 'starred' : ''}`} aria-label={preset ? 'Remove preset' : 'Save as preset'} disabled={!source} onPointerDown={stop} onClick={toggleStar}><IconStar filled={!!preset} /></button>
        <button aria-label="Next preset" onPointerDown={stop} onClick={() => run(actions.nextPreset())}><IconNext /></button>
      </>
    );
  }
  return (
    <div className="f-bar" onPointerDown={stop}>
      {transport}
      <div className="vsep" />
      <VolumeCell />
    </div>
  );
}
