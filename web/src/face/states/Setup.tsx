import { useEffect, useRef } from 'react';
import QRCode from 'qrcode';
import { useState } from '../../shared/store';
import { fmtTime, useNow } from '../../shared/time';

export function Setup() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  const ref = useRef<HTMLCanvasElement>(null);
  const setup = s.face.setup;
  useEffect(() => {
    if (ref.current && setup?.qr_payload) QRCode.toCanvas(ref.current, setup.qr_payload, { width: 220, margin: 1, color: { dark: '#000000', light: '#ffffff' } }).catch(() => {});
  }, [setup?.qr_payload]);
  return (
    <div className="face-screen fade-in">
      <div className="flex items-center justify-between"><div className="face-chip">No network</div><div className="face-time small tnum">{t.hm}</div></div>
      <div className="flex-1 flex items-center gap-[6vmin]">
        <canvas ref={ref} className="rounded-[2vmin] bg-white p-[1vmin]" />
        <div>
          <div className="text-[6vmin] font-semibold">Connect to set up</div>
          <div className="text-[4vmin] text-muted mt-[2vmin]">Wi-Fi: <b className="text-fg">{setup?.ssid}</b>{setup?.password && <> · password <b className="text-fg">{setup.password}</b></>}</div>
          <div className="text-[4vmin] text-muted mt-[1vmin]">then open <b className="text-fg">{setup?.url}</b></div>
        </div>
      </div>
    </div>
  );
}
