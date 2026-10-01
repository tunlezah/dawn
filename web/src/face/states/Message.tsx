import { useState } from '../../shared/store';

export function Message() {
  const s = useState();
  const m = s.face.message;
  const color = m?.level === 'error' ? 'text-err' : m?.level === 'warning' ? 'text-warn' : 'text-fg';
  return (
    <div className="face-screen fade-in items-center justify-center text-center">
      <div className={`text-[7vmin] font-semibold ${color}`}>{m?.title}</div>
      {m?.body && <div className="text-[4.2vmin] text-muted mt-[2vmin] max-w-[80vw]">{m.body}</div>}
      <div className="text-[3.4vmin] text-faint mt-[4vmin]">tap to dismiss</div>
    </div>
  );
}
