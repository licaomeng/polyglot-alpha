// Test browser zoom 50% and 200% on home + event-detail at 1920x1080
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const OUT_DIR = path.resolve(__dirname, '../../screenshots/wave1-p3');
const BASE = 'http://localhost:3001';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const cases = [
    { url: '/', name: 'home' },
    { url: '/events/112', name: 'event-detail' },
  ];
  const zooms = [0.5, 2.0];
  for (const c of cases) {
    for (const z of zooms) {
      const ctx = await browser.newContext({
        viewport: { width: 1920, height: 1080 },
        colorScheme: 'dark',
        deviceScaleFactor: 1,
      });
      const page = await ctx.newPage();
      await page.goto(BASE + c.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(2000);
      // apply zoom via CSS — Chromium honors zoom CSS property on body
      await page.addStyleTag({ content: `html { zoom: ${z}; }` });
      await page.waitForTimeout(500);
      const metrics = await page.evaluate(() => ({
        docW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth,
        hasH: document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
      }));
      const file = path.join(OUT_DIR, `zoom-${c.name}-${Math.round(z*100)}.png`);
      await page.screenshot({ path: file, fullPage: false });
      console.log(`${c.name} zoom=${z} hScroll=${metrics.hasH} docW=${metrics.docW} clientW=${metrics.clientW}`);
      await ctx.close();
    }
  }
  await browser.close();
})();
