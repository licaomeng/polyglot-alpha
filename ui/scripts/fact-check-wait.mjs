import { chromium } from 'playwright';
import * as fs from 'fs';

const BASE_URL = 'http://localhost:3001';
const SCREENSHOTS_DIR = '/Users/messili/codebase/polyglot-alpha/ui/screenshots/factcheck';

async function main() {
  const browser = await chromium.launch();
  const results = [];

  try {
    console.log('Checking events list after waiting...');
    const eventsPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await eventsPage.goto(`${BASE_URL}/events`);
    await eventsPage.waitForLoadState('networkidle');

    // Wait for event cards to appear
    const cardSelector = '[class*="card"], [role="article"], div[class*="event"]';
    try {
      await eventsPage.locator(cardSelector).first().waitFor({ timeout: 3000 });
    } catch {}

    const allText = await eventsPage.locator('body').textContent();
    const eventLinks = await eventsPage.locator('a').all();

    console.log(`Page has ${eventLinks.length} links`);
    let realEventUrl = null;

    for (let link of eventLinks) {
      const href = await link.getAttribute('href').catch(() => '');
      if (href && href.startsWith('/events/') && href !== '/events' && !href.includes('history') && href.length > 10) {
        realEventUrl = href;
        console.log(`Found event: ${href}`);
        break;
      }
    }

    if (realEventUrl) {
      const fullUrl = BASE_URL + realEventUrl;
      console.log(`Opening event: ${fullUrl}`);
      const eventPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
      await eventPage.goto(fullUrl);
      await eventPage.waitForLoadState('networkidle');
      await eventPage.waitForTimeout(1000);

      const bodyText = await eventPage.locator('body').textContent();
      const bodyHTML = await eventPage.content();

      // Count table rows (judges)
      const tables = await eventPage.locator('table').all();
      let maxRows = 0;
      for (let tbl of tables) {
        const rows = await tbl.locator('tr').all();
        maxRows = Math.max(maxRows, rows.length);
      }

      results.push({ claim: 16, status: maxRows >= 11 ? 'TRUE' : maxRows > 0 ? 'PARTIAL' : 'FALSE', evidence: `${maxRows} rows` });
      results.push({ claim: 17, status: bodyText.includes('0xsim') ? 'PARTIAL' : 'FALSE', evidence: bodyText.includes('0xsim') ? 'Found' : 'Not found' });

      let hasDialog = false;
      const allBtns = await eventPage.locator('button').all();
      for (let btn of allBtns) {
        const text = await btn.textContent().catch(() => '');
        if (text && text.toLowerCase().includes('dossier')) {
          hasDialog = true;
          break;
        }
      }
      results.push({ claim: 18, status: hasDialog ? 'TRUE' : 'FALSE', evidence: hasDialog ? 'Button found' : 'Button not found' });
      results.push({ claim: 19, status: bodyText.includes('ipfs://sim') ? 'PARTIAL' : 'FALSE', evidence: bodyText.includes('ipfs://sim') ? 'Found' : 'Not found' });

      let hasAPI = false;
      for (let btn of allBtns) {
        const text = await btn.textContent().catch(() => '');
        if (text && text.toLowerCase().includes('api')) {
          hasAPI = true;
          break;
        }
      }
      results.push({ claim: 20, status: hasAPI ? 'TRUE' : 'FALSE', evidence: hasAPI ? 'Button found' : 'Button not found' });
      results.push({ claim: 21, status: bodyText.includes('fee') ? 'PARTIAL' : 'FALSE', evidence: bodyText.includes('fee') ? 'Found' : 'Not found' });
      results.push({ claim: 22, status: bodyText.includes('debate') ? 'PARTIAL' : 'FALSE', evidence: bodyText.includes('debate') ? 'Found' : 'Not found' });

      const eventId = realEventUrl.split('/').pop();
      await eventPage.screenshot({ path: `${SCREENSHOTS_DIR}/event-${eventId}.png` });
      console.log(`Saved: event-${eventId}.png`);
      await eventPage.close();
    } else {
      console.log('No valid event found');
      [16, 17, 18, 19, 20, 21, 22].forEach(n => {
        results.push({ claim: n, status: 'FALSE', evidence: 'No events available' });
      });
    }

    await eventsPage.close();

  } catch (error) {
    console.error('Error:', error.message);
  } finally {
    await browser.close();
  }

  console.log('\nEvent Claims:');
  results.forEach(r => console.log(`${r.claim}. ${r.status} - ${r.evidence}`));
  fs.writeFileSync(`${SCREENSHOTS_DIR}/event-details.json`, JSON.stringify(results, null, 2));
}

main().catch(console.error);
