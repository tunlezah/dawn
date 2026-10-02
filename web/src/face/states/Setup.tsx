import { useEffect, useRef } from 'react';
import QRCode from 'qrcode';
import { useState } from '../../shared/store';
import { FaceHeader } from '../components/Header';
import { IconWifiOff } from '../components/icons';

export function Setup() {
  const s = useState();
  const ref = useRef<HTMLCanvasElement>(null);
  const setup = s.face.setup;
  useEffect(() => {
    if (ref.current && setup?.qr_payload) QRCode.toCanvas(ref.current, setup.qr_payload, { width: 220, margin: 1, color: { dark: '#000000', light: '#ffffff' } }).catch(() => {});
  }, [setup?.qr_payload]);
  return (
    <div className="face-screen fade-in">
      <FaceHeader left={<span className="face-chip"><IconWifiOff />No network</span>} />
      <div className="f-body">
        <canvas ref={ref} className="rounded-[3vmin] bg-white p-[1.5vmin] flex-none" />
        <div className="f-info">
          <div className="f-title">Connect to set up</div>
          <div className="f-sub">Wi-Fi <b className="text-fg">{setup?.ssid}</b></div>
          {setup?.password && <div className="f-sub">Password <b className="text-fg">{setup.password}</b></div>}
          <div className="f-sub mt-[1vmin]">then open <b className="text-fg">{setup?.url}</b></div>
        </div>
      </div>
    </div>
  );
}
