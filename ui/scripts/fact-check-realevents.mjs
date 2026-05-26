import { chromium } from 'playwright';
import * as fs from 'fs';

const BASE_URL = 'http://localhost:3001';
const SCREENSHOTS_DIR = '/Users/messili/codebase/polyglot-alpha/ui/screenshots/factcheck';

async function main() {
  const browser = await chromium.launch();
  const results = [];

  try {
    // Find real events first
    console.log('Finding real events...');
    const eventsListPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await eventsListPage.goto(`${BASE_URL}/events`);
    await eventsListPage.waitForLoadState('networkidle');
    await eventsListPage.waitForTimeout(500);

    const allLinks = await eventsListPage.locator('a').all();
    let realEventUrl = null;
    for (let link of allLinks) {
      const href = await link.getAttribute('href').catch(() => '');
      const text = await link.textContent().catch(() => '');
      if (href && href.startsWith('/events/') && href !== '/events' && !href.includes('history')) {
        console.log(`Found event link: ${href} (text: "${text}")`);
        realEventUrl = href;
        break;
      }
    }

    if (!realEventUrl) {
      console.log('No events found - triggering a live demo first');
      // Try to trigger a live demo
      const homePage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
      await homePage.goto(BASE_URL);
      await homePage.waitForLoadState('networkidle');

      const triggerBtn = await homePage.locator('button').filter({ hasText: /Trigger/ }).first();
      if (await triggerBtn.isVisible()) {
        console.log('Clicking trigger button...');
        await triggerBtn.click();
        await homePage.waitForTimeout(3000);
      }
      await homePage.close();

      // Now retry events list
      await eventsListPage.reload();
      await eventsListPage.waitForLoadState('networkidle');
      await eventsListPage.waitForTimeout(500);

      const allLinks2 = await eventsListPage.locator('a').all();
      for (let link of allLinks2) {
        const href = await link.getAttribute('href').catch(() => '');
        if (href && href.startsWith('/events/') && href !== '/events') {
          realEventUrl = href;
          console.log(`Found event after trigger: ${href}`);
          break;
        }
      }
    }

    if (realEventUrl) {
      const fullEventUrl = BASE_URL + realEventUrl;
      console.log(`Opening: ${fullEventUrl}`);
      const eventPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
      await eventPage.goto(fullEventUrl);
      await eventPage.waitForLoadState('networkidle');
      await eventPage.waitForTimeout(1000);

      const eventText = await eventPage.locator('body').textContent().catch(() => '');

      // Claim 16: Judge dossier
      const tables = await eventPage.locator('table').all();
      let judgeCount = 0;
      for (let tbl of tables) {
        const rows = await tbl.locator('tbody tr, tr').all();
        if (rows.length > judgeCount) judgeCount = rows.length;
      }
      console.log(`16. Judge rows: ${judgeCount}`);
      results.push({ claim: 16, status: judgeCount >= 11 ? 'TRUE' : judgeCount > 0 ? 'PARTIAL' : 'FALSE', evidence: `${judgeCount}` });

      // Claim 17: Attestation
      const hasSimTx = eventText.includes('0xsim');
      console.log(`17. 0xsim found: ${hasSimTx}`);
      results.push({ claim: 17, status: hasSimTx ? 'PARTIAL' : 'FALSE', evidence: hasSimTx ? 'Yes' : 'No' });

      // Claim 18: Dossier button
      const buttons = await eventPage.locator('button').all();
      let hasDossierBtn = false;
      for (let btn of buttons) {
        const text = await btn.textContent().catch(() => '');
        if (text && (text.toLowerCase().includes('dossier') || text.toLowerCase().includes('raw'))) {
          hasDossierBtn = true;
          break;
        }
      }
      console.log(`18. Dossier button: ${hasDossierBtn}`);
      results.push({ claim: 18, status: hasDossierBtn ? 'TRUE' : 'FALSE', evidence: hasDossierBtn ? 'Yes' : 'No' });

      // Claim 19: IPFS
      const hasIPFS = eventText.includes('ipfs://sim');
      console.log(`19. IPFS: ${hasIPFS}`);
      results.push({ claim: 19, status: hasIPFS ? 'PARTIAL' : 'FALSE', evidence: hasIPFS ? 'Yes' : 'No' });

      // Claim 20: API button
      let hasAPIBtn = false;
      for (let btn of buttons) {
        const text = await btn.textContent().catch(() => '');
        if (text && (text.toLowerCase().includes('api') || text.toLowerCase().includes('payload'))) {
          hasAPIBtn = true;
          break;
        }
      }
      console.log(`20. API button: ${hasAPIBtn}`);
      results.push({ claim: 20, status: hasAPIBtn ? 'TRUE' : 'FALSE', evidence: hasAPIBtn ? 'Yes' : 'No' });

      // Claim 21: Fee split
      const hasFee = eventText.toLowerCase().includes('fee') || eventText.toLowerCase().includes('recipient');
      console.log(`21. Fee: ${hasFee}`);
      results.push({ claim: 21, status: hasFee ? 'PARTIAL' : 'FALSE', evidence: hasFee ? 'Yes' : 'No' });

      // Claim 22: Debate
      const hasDebate = eventText.toLowerCase().includes('debate') || eventText.toLowerCase().includes('agent');
      console.log(`22. Debate: ${hasDebate}`);
      results.push({ claim: 22, status: hasDebate ? 'PARTIAL' : 'FALSE', evidence: hasDebate ? 'Yes' : 'No' });

      const eventId = realEventUrl.split('/').pop();
      await eventPage.screenshot({ path: `${SCREENSHOTS_DIR}/event-${eventId}.png` });
      console.log(`Screenshot: event-${eventId}.png`);
      await eventPage.close();
    } else {
      console.log('No events available - marking claims 16-22 as FALSE');
      [16, 17, 18, 19, 20, 21, 22].forEach(n => {
        results.push({ claim: n, status: 'FALSE', evidence: 'No events' });
      });
    }

    await eventsListPage.close();

  } catch (error) {
    console.error('Error:', error.message);
  } finally {
    await browser.close();
  }

  console.log('\nEvent detail results:');
  results.forEach(r => console.log(`${r.claim}. ${r.status}`));
  fs.writeFileSync(`${SCREENSHOTS_DIR}/event-results.json`, JSON.stringify(results, null, 2));
}

main().catch(console.error);
