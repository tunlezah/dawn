import { useEffect, useRef } from 'react';
import QRCode from 'qrcode';
import { useState } from '../../shared/store';
import { FaceHeader } from '../components/Header';
import { IconWifiOff } from '../components/icons';

export function Setup() {
  const s = useState();
  const ref = useRef<HTMLCanvasElement>(null);
  const setup = s.face.setup;
  // At night the code is drawn dark on a dim amber card, never black on white: someone asked for it by tapping the
  // "No Wi-Fi" chip, and a phone camera still reads it at this contrast.
  const night = s.display.palette === 'night' || s.display.sleep;
  const light = night ? '#a8822e' : '#ffffff';
  useEffect(() => {
    if (ref.current && setup?.qr_payload) QRCode.toCanvas(ref.current, setup.qr_payload, { width: 220, margin: 1, color: { dark: '#000000', light } }).catch(() => {});
  }, [setup?.qr_payload, light]);
  return (
    <div className="face-screen fade-in">
      <FaceHeader left={<span className="face-chip"><IconWifiOff />No network</span>} />
      <div className="f-body">
        <canvas ref={ref} className="rounded-[3vmin] p-[1.5vmin] flex-none" style={{ background: light }} />
        <div className="f-info">
          <div className="f-title">Connect to set up</div>
          <div className="f-sub">Wi-Fi <b className="text-fg">{setup?.ssid}</b></div>
          {setup?.password && <div className="f-sub">Password <b className="text-fg">{setup.password}</b></div>}
          <div className="f-sub mt-[1vmin]">then open <b className="text-fg">{setup?.url}</b></div>
          {s.face.setup_open && <div className="f-tiny mt-[2vmin]">Dawn keeps looking for its Wi-Fi by itself. Tap to go back to the clock.</div>}
        </div>
      </div>
    </div>
  );
}
