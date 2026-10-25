// End-to-end: an alarm is due while dawn-core is frozen (SIGSTOP). The face must ring by itself 45 s after the alarm,
// then hand the ring back to core once it answers again. Prints a timeline; exits 0 only if every step was seen.
const { chromium } = require('playwright');
const { execSync } = require('child_process');

const base = 'http://127.0.0.1:8080';
const corePid = parseInt(process.env.CORE_PID, 10);
const alarmAt = process.env.ALARM_AT; // ISO local time with offset, as created
const t0 = Date.now();
const say = (...a) => console.log(((Date.now() - t0) / 1000).toFixed(1).padStart(6) + 's', ...a);

(async () => {
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const context = await browser.newContext({ viewport: { width: 800, height: 480 }, hasTouch: true });
  const page = await context.newPage();
  page.on('pageerror', (e) => say('pageerror', String(e)));
  await page.goto(base + '/face', { waitUntil: 'load' });
  const screen = async () => page.evaluate(() => ({
    cls: document.querySelector('.face-screen')?.className || '',
    beep: document.querySelector('.face-root')?.getAttribute('data-beep') || '',
    label: document.querySelector('.f-label')?.textContent || '',
    offline: !!document.querySelector('.dot-err'),
  }));
  // wait until the face knows the next alarm (it is in localStorage once heard)
  let heard = null;
  for (let i = 0; i < 60 && !heard; i++) {
    await page.waitForTimeout(1000);
    heard = await page.evaluate(() => { try { return JSON.parse(localStorage.getItem('dawn.face.backup') || 'null')?.heard?.next?.at || null; } catch { return null; } });
  }
  say('face has heard next alarm at', heard, '(expected', alarmAt + ')');
  if (!heard || Date.parse(heard) !== Date.parse(alarmAt)) { say('FAIL: face did not learn the alarm'); process.exit(1); }
  const due = Date.parse(alarmAt);
  // freeze core 25 s before the alarm
  const freezeIn = due - Date.now() - 25000;
  if (freezeIn > 0) await page.waitForTimeout(freezeIn);
  execSync(`kill -STOP ${corePid}`);
  say('core frozen (SIGSTOP)');
  let sawOffline = false, sawLocal = null, sawBeep = null, sawHandback = null;
  const deadline = due + 120000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(1000);
    const s = await screen();
    if (!sawOffline && s.offline) { sawOffline = true; say('face shows offline'); }
    if (!sawLocal && s.cls.includes('f-ring') && s.cls.includes('local')) { sawLocal = (Date.now() - due) / 1000; say(`FACE RINGS BY ITSELF ${sawLocal.toFixed(1)} s after the alarm: "${s.label}"`); }
    if (!sawBeep && s.beep === '1') { sawBeep = (Date.now() - due) / 1000; say(`beeper on ${sawBeep.toFixed(1)} s after the alarm`); }
    if (sawLocal && sawBeep && Date.now() - due > 60000 && !sawHandback) {
      execSync(`kill -CONT ${corePid}`);
      say('core thawed (SIGCONT)');
      for (let i = 0; i < 40; i++) {
        await page.waitForTimeout(1000);
        const r = await screen();
        if (r.cls.includes('f-ring') && !r.cls.includes('local')) { sawHandback = (Date.now() - due) / 1000; say(`core rings it now (${sawHandback.toFixed(1)} s after the alarm): "${r.label}", beep=${r.beep}`); break; }
      }
      break;
    }
  }
  try { execSync(`kill -CONT ${corePid}`); } catch {}
  const ev = JSON.parse(execSync(`curl -s ${base}/api/events?limit=30`).toString()).filter((e) => ['alarm_fire', 'alarm_missed', 'ring_start', 'ring_fallback'].includes(e.kind)).map((e) => `${e.kind}@${e.at.slice(11, 19)}${e.late_s !== undefined ? ' late_s=' + e.late_s : ''}${e.tier ? ' tier=' + e.tier : ''}`);
  say('core events:', ev.join(' | '));
  execSync(`curl -s -X POST ${base}/api/alarms/stop`);
  await browser.close();
  const okAll = sawOffline && sawLocal !== null && sawBeep !== null && sawHandback !== null;
  say(okAll ? 'PASS' : 'FAIL', JSON.stringify({ sawOffline, sawLocal, sawBeep, sawHandback }));
  process.exit(okAll ? 0 : 1);
})().catch((e) => { say('error', e); try { execSync(`kill -CONT ${corePid}`); } catch {} process.exit(1); });
