import { useState } from '../../shared/store';
import { FaceHeader } from '../components/Header';

export function Message() {
  const s = useState();
  const m = s.face.message;
  const color = m?.level === 'error' ? 'text-err' : m?.level === 'warning' ? 'text-warn' : '';
  return (
    <div className="face-screen fade-in">
      <FaceHeader left={m?.level && m.level !== 'info' ? <span className={`face-chip ${color}`}>{m.level}</span> : null} />
      <div className="f-body" style={{ flexDirection: 'column', justifyContent: 'center', textAlign: 'center', gap: '2vmin' }}>
        <div className={`f-title ${color}`}>{m?.title}</div>
        {m?.body && <div className="f-sub max-w-[80vw]">{m.body}</div>}
        <div className="f-tiny mt-[2vmin]">tap to dismiss</div>
      </div>
    </div>
  );
}
