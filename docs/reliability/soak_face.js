// Face soak: the kiosk page open in Chromium while core's state churns; samples the page's heap and DOM every 30 s
// (after a forced GC, so what is reported is reachable memory, not garbage).
const { chromium } = require('playwright');

(async () => {
  const minutes = parseFloat(process.env.SOAK_MINUTES || '12');
  const url = process.env.FACE_URL || 'http://127.0.0.1:8080/face';
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const context = await browser.newContext({ viewport: { width: 800, height: 480 }, hasTouch: true });
  const page = await context.newPage();
  page.on('pageerror', (e) => console.error('pageerror', String(e)));
  page.on('console', (m) => { if (m.type() === 'error') console.error('console.error', m.text()); });
  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable');
  await cdp.send('HeapProfiler.enable');
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForTimeout(5000);
  const t0 = Date.now();
  console.log('t_s,jsheap_used_mb,jsheap_total_mb,nodes,js_listeners,documents,frames,layout_count,style_recalcs,mode,beep');
  while (Date.now() - t0 < minutes * 60000) {
    await page.waitForTimeout(30000);
    await cdp.send('HeapProfiler.collectGarbage').catch(() => {});
    const { metrics } = await cdp.send('Performance.getMetrics');
    const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
    const mode = await page.evaluate(() => (document.querySelector('.face-screen')?.className || '').split(' ').slice(0, 2).join('/')).catch(() => '?');
    const beep = await page.evaluate(() => document.querySelector('.face-root')?.getAttribute('data-beep') || '').catch(() => '?');
    console.log([((Date.now() - t0) / 1000).toFixed(0), (m.JSHeapUsedSize / 1048576).toFixed(2), (m.JSHeapTotalSize / 1048576).toFixed(2), m.Nodes, m.JSEventListeners, m.Documents, m.Frames, m.LayoutCount, m.RecalcStyleCount, mode, beep].join(','));
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
