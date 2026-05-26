// Retry the two timed-out cells with a less strict wait
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const OUT_DIR = path.resolve(__dirname, '../../screenshots/wave1-p3');
const BASE = 'http://localhost:3001';

const TARGETS = [
  { name: 'home',         url: '/',             vp: { w: 1280, h: 800, name: '1280x800' } },
  { name: 'event-detail', url: '/events/112',   vp: { w: 1280, h: 800, name: '1280x800' } },
];

(async () => {
  const browser = await chromium.launch({ headless: true });

  // pre-warm at default size
  const warm = await browser.newContext();
  const wp = await warm.newPage();
  await wp.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(()=>{});
  await wp.goto(BASE + '/events/112', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(()=>{});
  await warm.close();

  const existing = JSON.parse(fs.readFileSync(path.join(OUT_DIR, 'metrics.json')));

  for (const t of TARGETS) {
    const ctx = await browser.newContext({
      viewport: { width: t.vp.w, height: t.vp.h },
      deviceScaleFactor: 1,
      colorScheme: 'dark',
    });
    const page = await ctx.newPage();
    const consoleMsgs = [];
    page.on('pageerror', e => consoleMsgs.push('PAGEERROR:' + e.message));
    page.on('console', m => { if (m.type() === 'error') consoleMsgs.push('CONSOLE:' + m.text()); });

    const url = BASE + t.url;
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(2500);

    const metrics = await page.evaluate(() => {
      const doc = document.documentElement;
      const body = document.body;
      const main = document.querySelector('main') || body;
      const mainRect = main.getBoundingClientRect();
      const header = document.querySelector('header');
      const footer = document.querySelector('footer');
      const headerRect = header ? header.getBoundingClientRect() : null;
      const footerRect = footer ? footer.getBoundingClientRect() : null;
      const hasHScroll = doc.scrollWidth > doc.clientWidth + 1;
      const overflowing = [];
      document.querySelectorAll('main *').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.right > window.innerWidth + 2 || r.left < -2) {
          const txt = (el.textContent || '').trim().slice(0, 50);
          if (txt) overflowing.push({ tag: el.tagName, left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width), text: txt });
        }
      });
      return {
        viewportW: window.innerWidth, viewportH: window.innerHeight,
        docScrollW: doc.scrollWidth, docClientW: doc.clientWidth,
        hasHScroll, scrollDelta: doc.scrollWidth - doc.clientWidth,
        mainW: Math.round(mainRect.width), mainLeft: Math.round(mainRect.left), mainRight: Math.round(mainRect.right),
        sideWhitespacePx: Math.round((window.innerWidth - mainRect.width) / 2),
        headerH: headerRect ? Math.round(headerRect.height) : null,
        headerSticky: header ? getComputedStyle(header).position : null,
        footerTop: footerRect ? Math.round(footerRect.top) : null,
        bg: getComputedStyle(body).backgroundColor, color: getComputedStyle(body).color,
        overflowing: overflowing.slice(0, 5),
      };
    });

    const file = path.join(OUT_DIR, `${t.name}_${t.vp.name}.png`);
    await page.screenshot({ path: file, fullPage: false });

    // update metrics.json record
    for (const r of existing) {
      if (r.page === t.name && r.viewport === t.vp.name) {
        r.metrics = metrics; r.status = resp ? resp.status() : null; r.errors = [];
        r.consoleMsgs = consoleMsgs.slice(0, 5); r.screenshot = file;
      }
    }
    console.log(`[retry done] ${t.name} @ ${t.vp.name}  mainW=${metrics.mainW} side=${metrics.sideWhitespacePx} overflow=${metrics.overflowing.length}`);
    await ctx.close();
  }

  await browser.close();
  fs.writeFileSync(path.join(OUT_DIR, 'metrics.json'), JSON.stringify(existing, null, 2));
})();
