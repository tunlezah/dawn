import { useState } from '../../shared/store';
import { fmtTime, useNow } from '../../shared/time';

// Full-white screen before an alarm (light wake). The backlight is forced to 100 % by core.
export function LightWake() {
  const s = useState();
  const now = useNow();
  const t = fmtTime(now, s.tz, s.settings.clock_24h);
  return (
    <div className="lightwake flex items-end justify-end p-[4.2vmin] fade-in">
      <div className="face-time f-clock" style={{ color: '#c8c8c8' }}>{t.hm}</div>
    </div>
  );
}
