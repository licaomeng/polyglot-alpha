// Wave1-P3 layout sweep: 6 pages x 3 viewports = 18 screenshots
// Also collects layout diagnostics per page/viewport.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const OUT_DIR = path.resolve(__dirname, '../../screenshots/wave1-p3');
const BASE = 'http://localhost:3001';

const VIEWPORTS = [
  { name: '1280x800',   w: 1280, h: 800 },
  { name: '1920x1080',  w: 1920, h: 1080 },
  { name: '3840x2160',  w: 3840, h: 2160 },
];

const PAGES = [
  { name: 'home',         url: '/' },
  { name: 'events',       url: '/events' },
  { name: 'event-detail', url: '/events/112' },
  { name: 'leaderboard',  url: '/leaderboard' },
  { name: 'operators',    url: '/operators' },
  { name: 'about',        url: '/about' },
];

(async () => {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const results = [];

  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({
      viewport: { width: vp.w, height: vp.h },
      deviceScaleFactor: 1,
      colorScheme: 'dark',
    });
    const page = await ctx.newPage();

    for (const p of PAGES) {
      const fileBase = `${p.name}_${vp.name}`;
      const url = BASE + p.url;
      const diag = { page: p.name, viewport: vp.name, url, errors: [], warnings: [] };
      try {
        const consoleMsgs = [];
        page.on('pageerror', e => consoleMsgs.push('PAGEERROR:' + e.message));
        page.on('console', m => { if (m.type() === 'error') consoleMsgs.push('CONSOLE:' + m.text()); });

        const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
        diag.status = resp ? resp.status() : null;

        // give framer-motion + recharts time
        await page.waitForTimeout(1200);

        // collect layout metrics
        const metrics = await page.evaluate(() => {
          const doc = document.documentElement;
          const body = document.body;
          const main = document.querySelector('main') || body;
          const mainRect = main.getBoundingClientRect();
          const header = document.querySelector('header');
          const footer = document.querySelector('footer');
          const headerRect = header ? header.getBoundingClientRect() : null;
          const footerRect = footer ? footer.getBoundingClientRect() : null;

          // largest content container width inside <main>
          const candidates = main.querySelectorAll('div, section, article');
          let maxContent = { w: 0, cls: '', selector: '' };
          candidates.forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > maxContent.w && r.height > 100) {
              maxContent = { w: r.width, cls: el.className || '', selector: el.tagName + (el.id ? '#' + el.id : '') };
            }
          });

          // detect horizontal scroll
          const hasHScroll = doc.scrollWidth > doc.clientWidth + 1;
          const scrollDelta = doc.scrollWidth - doc.clientWidth;

          // check for elements overflowing viewport horizontally
          const overflowing = [];
          document.querySelectorAll('main *').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.right > window.innerWidth + 2 || r.left < -2) {
              const txt = (el.textContent || '').trim().slice(0, 50);
              if (txt) overflowing.push({
                tag: el.tagName, left: Math.round(r.left), right: Math.round(r.right),
                width: Math.round(r.width), text: txt
              });
            }
          });

          // computed bg/color for theme
          const bg = getComputedStyle(body).backgroundColor;
          const color = getComputedStyle(body).color;

          // sticky header check
          const headerSticky = header ? getComputedStyle(header).position : null;

          // measure horizontal whitespace around main content at body level
          const bodyW = body.getBoundingClientRect().width;
          const sideWhitespace = headerRect ? Math.max(0, headerRect.left) + Math.max(0, bodyW - headerRect.right) : 0;

          return {
            viewportW: window.innerWidth,
            viewportH: window.innerHeight,
            docScrollW: doc.scrollWidth,
            docClientW: doc.clientWidth,
            hasHScroll, scrollDelta,
            mainW: Math.round(mainRect.width),
            mainLeft: Math.round(mainRect.left),
            mainRight: Math.round(mainRect.right),
            sideWhitespacePx: Math.round((window.innerWidth - mainRect.width) / 2),
            maxContent,
            headerH: headerRect ? Math.round(headerRect.height) : null,
            headerSticky,
            footerTop: footerRect ? Math.round(footerRect.top) : null,
            footerVisible: footerRect ? footerRect.top < window.innerHeight + 200 : null,
            bg, color,
            overflowing: overflowing.slice(0, 5),
          };
        });
        diag.metrics = metrics;
        diag.consoleMsgs = consoleMsgs.slice(0, 5);

        // screenshot — full page, but cap height for 4K (too big)
        const screenshotPath = path.join(OUT_DIR, `${fileBase}.png`);
        await page.screenshot({ path: screenshotPath, fullPage: false });
        diag.screenshot = screenshotPath;
      } catch (e) {
        diag.errors.push(e.message);
      }
      results.push(diag);
      console.log(`[done] ${p.name} @ ${vp.name}`);
    }
    await ctx.close();
  }

  await browser.close();

  fs.writeFileSync(path.join(OUT_DIR, 'metrics.json'), JSON.stringify(results, null, 2));
  console.log('Wrote ' + path.join(OUT_DIR, 'metrics.json'));
})();
