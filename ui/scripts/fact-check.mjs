import { chromium } from 'playwright';
import * as fs from 'fs';

const BASE_URL = 'http://localhost:3001';
const SCREENSHOTS_DIR = '/Users/messili/codebase/polyglot-alpha/ui/screenshots/factcheck';

async function main() {
  const browser = await chromium.launch();
  const results = [];

  try {
    // ===== PAGE 1: HOME /
    console.log('Checking home page...');
    const homePage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await homePage.goto(BASE_URL);
    await homePage.waitForLoadState('networkidle');
    await homePage.waitForTimeout(500);

    // Claim 1: LIVE/MOCK chip
    try {
      const modeButtons = await homePage.locator('button').filter({ hasText: /LIVE|MOCK/ }).first();
      const visible = await modeButtons.isVisible().catch(() => false);
      const text = visible ? await modeButtons.textContent() : 'NOT_FOUND';
      results.push({ claim: 1, status: visible ? 'TRUE' : 'FALSE', evidence: `Button: "${text}"` });
    } catch (e) {
      results.push({ claim: 1, status: 'FALSE', evidence: 'Selector error' });
    }

    // Claim 2: 5 flags
    try {
      const flags = await homePage.locator('img').filter({ hasText: /flag|language|lang/ }).all();
      results.push({ claim: 2, status: flags.length >= 5 ? 'TRUE' : 'PARTIAL', evidence: `Found ${flags.length} flags` });
    } catch {
      results.push({ claim: 2, status: 'FALSE', evidence: 'No flag elements found' });
    }

    // Claim 3: Hero text
    const heroText = await homePage.locator('main h1, main h2').first().textContent().catch(() => '');
    const hasHero = heroText.toLowerCase().includes('decentralized') && heroText.toLowerCase().includes('cross-language');
    results.push({ claim: 3, status: hasHero ? 'TRUE' : 'FALSE', evidence: `Text: "${heroText.substring(0, 60)}..."` });

    // Claim 4: Trigger demo buttons
    try {
      const triggerBtn = await homePage.locator('button').filter({ hasText: /Trigger|Demo|Start/ }).first();
      const visible = await triggerBtn.isVisible().catch(() => false);
      const text = visible ? await triggerBtn.textContent() : 'NOT_FOUND';
      results.push({ claim: 4, status: visible ? 'TRUE' : 'FALSE', evidence: `Button: "${text}"` });
    } catch {
      results.push({ claim: 4, status: 'FALSE', evidence: 'No trigger button found' });
    }

    // Claim 5: Mermaid diagram
    try {
      const mermaid = await homePage.locator('svg').first();
      const visible = await mermaid.isVisible().catch(() => false);
      results.push({ claim: 5, status: visible ? 'PARTIAL' : 'FALSE', evidence: `Diagram visible: ${visible}` });
    } catch {
      results.push({ claim: 5, status: 'FALSE', evidence: 'No diagram found' });
    }

    // Claim 6: React Flow DAG
    try {
      const nodes = await homePage.locator('[class*="node"]').all();
      results.push({ claim: 6, status: nodes.length === 11 ? 'TRUE' : 'PARTIAL', evidence: `Found ${nodes.length} nodes (expected 11)` });
    } catch {
      results.push({ claim: 6, status: 'FALSE', evidence: 'No DAG found' });
    }

    // Claim 7: Why now / Problem section
    try {
      const section = await homePage.locator('h2, h3').filter({ hasText: /Why|Problem/ }).first();
      const visible = await section.isVisible().catch(() => false);
      results.push({ claim: 7, status: visible ? 'TRUE' : 'FALSE', evidence: `Section visible: ${visible}` });
    } catch {
      results.push({ claim: 7, status: 'FALSE', evidence: 'No section found' });
    }

    // Claim 8: Contract addresses
    try {
      const contracts = await homePage.locator('span, code').filter({ hasText: /0x[a-f0-9]/ }).all();
      results.push({ claim: 8, status: contracts.length >= 5 ? 'TRUE' : 'PARTIAL', evidence: `Found ${contracts.length} contracts` });
    } catch {
      results.push({ claim: 8, status: 'FALSE', evidence: 'No contracts found' });
    }

    await homePage.screenshot({ path: `${SCREENSHOTS_DIR}/home.png` });
    await homePage.close();

    // ===== PAGE 2: /ABOUT
    console.log('Checking about page...');
    const aboutPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await aboutPage.goto(`${BASE_URL}/about`);
    await aboutPage.waitForLoadState('networkidle');

    // Claim 9: 5-phase pipeline
    try {
      const text = await aboutPage.locator('main').textContent();
      const phases = (text.match(/phase/gi) || []).length;
      results.push({ claim: 9, status: phases >= 5 ? 'TRUE' : 'FALSE', evidence: `Found ${phases} phase mentions` });
    } catch {
      results.push({ claim: 9, status: 'FALSE', evidence: 'Error reading text' });
    }

    // Claim 10: How it works section
    try {
      const section = await aboutPage.locator('h2, h3').filter({ hasText: /How/ }).first();
      const visible = await section.isVisible().catch(() => false);
      const text = visible ? await section.textContent() : 'NOT_FOUND';
      results.push({ claim: 10, status: visible ? 'TRUE' : 'FALSE', evidence: `Section: "${text}"` });
    } catch {
      results.push({ claim: 10, status: 'FALSE', evidence: 'No section found' });
    }

    // Claim 11: Glossary
    try {
      const items = await aboutPage.locator('[class*="glossary"], [class*="component"], dl dt, li').all();
      results.push({ claim: 11, status: items.length >= 11 ? 'TRUE' : 'PARTIAL', evidence: `Found ${items.length} items` });
    } catch {
      results.push({ claim: 11, status: 'FALSE', evidence: 'No glossary found' });
    }

    await aboutPage.screenshot({ path: `${SCREENSHOTS_DIR}/about.png` });
    await aboutPage.close();

    // ===== PAGE 3: /OPERATORS
    console.log('Checking operators page...');
    const operatorsPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await operatorsPage.goto(`${BASE_URL}/operators`);
    await operatorsPage.waitForLoadState('networkidle');

    // Claim 12: 3 seeder cards
    try {
      const cards = await operatorsPage.locator('[class*="card"], div[role="article"]').all();
      results.push({ claim: 12, status: cards.length >= 3 ? 'TRUE' : 'PARTIAL', evidence: `Found ${cards.length} cards` });
    } catch {
      results.push({ claim: 12, status: 'FALSE', evidence: 'No cards found' });
    }

    // Claim 13: wins/bids 26%
    try {
      const winsBids = await operatorsPage.locator('text=/wins|bids/i').first();
      const visible = await winsBids.isVisible().catch(() => false);
      results.push({ claim: 13, status: visible ? 'TRUE' : 'FALSE', evidence: `Visible: ${visible}` });
    } catch {
      results.push({ claim: 13, status: 'FALSE', evidence: 'No wins/bids found' });
    }

    // Claim 14: EMA dashed border
    try {
      const ema = await operatorsPage.locator('text=/EMA/').first();
      const visible = await ema.isVisible().catch(() => false);
      results.push({ claim: 14, status: visible ? 'PARTIAL' : 'FALSE', evidence: `EMA visible: ${visible}` });
    } catch {
      results.push({ claim: 14, status: 'FALSE', evidence: 'No EMA found' });
    }

    // Claim 15: Buttons
    try {
      const allBtns = await operatorsPage.locator('button').all();
      const btnTexts = await Promise.all(allBtns.slice(0, 20).map(b => b.textContent().catch(() => '')));
      const hasAll = btnTexts.join('').includes('Claim') && btnTexts.join('').includes('Withdraw') && btnTexts.join('').includes('Register');
      results.push({ claim: 15, status: hasAll ? 'TRUE' : 'PARTIAL', evidence: `Button count: ${allBtns.length}` });
    } catch {
      results.push({ claim: 15, status: 'FALSE', evidence: 'Error checking buttons' });
    }

    await operatorsPage.screenshot({ path: `${SCREENSHOTS_DIR}/operators.png` });
    await operatorsPage.close();

    // ===== PAGE 4: EVENT DETAIL
    console.log('Checking event detail...');
    const eventsPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await eventsPage.goto(`${BASE_URL}/events`);
    await eventsPage.waitForLoadState('networkidle');

    // Try to find event
    const eventLink = await eventsPage.locator('a').filter({ hasText: /[0-9]/ }).first();
    let eventUrl = null;
    try {
      eventUrl = await eventLink.getAttribute('href');
    } catch {}

    if (eventUrl) {
      const fullUrl = eventUrl.startsWith('/') ? BASE_URL + eventUrl : BASE_URL + '/' + eventUrl;
      console.log(`Opening event: ${fullUrl}`);
      const eventPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
      await eventPage.goto(fullUrl);
      await eventPage.waitForLoadState('networkidle');
      await eventPage.waitForTimeout(1000);

      // Claim 16: Judge dossier
      try {
        const judges = await eventPage.locator('tr, [class*="judge"]').all();
        const count = judges.length;
        results.push({ claim: 16, status: count >= 11 ? 'TRUE' : 'PARTIAL', evidence: `Found ${count} judge rows` });
      } catch {
        results.push({ claim: 16, status: 'FALSE', evidence: 'No judges found' });
      }

      // Claim 17: Attestation tx
      try {
        const tx = await eventPage.locator('span, code').filter({ hasText: /0xsim/ }).first();
        const visible = await tx.isVisible().catch(() => false);
        results.push({ claim: 17, status: visible ? 'PARTIAL' : 'FALSE', evidence: `Tx visible: ${visible}` });
      } catch {
        results.push({ claim: 17, status: 'FALSE', evidence: 'No tx found' });
      }

      // Claim 18: Dossier button
      try {
        const btn = await eventPage.locator('button').filter({ hasText: /dossier|raw|JSON/i }).first();
        const visible = await btn.isVisible().catch(() => false);
        results.push({ claim: 18, status: visible ? 'TRUE' : 'FALSE', evidence: `Button visible: ${visible}` });
      } catch {
        results.push({ claim: 18, status: 'FALSE', evidence: 'No button found' });
      }

      // Claim 19: IPFS link
      try {
        const ipfs = await eventPage.locator('text=/ipfs:/i').first();
        const visible = await ipfs.isVisible().catch(() => false);
        results.push({ claim: 19, status: visible ? 'PARTIAL' : 'FALSE', evidence: `IPFS visible: ${visible}` });
      } catch {
        results.push({ claim: 19, status: 'FALSE', evidence: 'No IPFS found' });
      }

      // Claim 20: API payload button
      try {
        const btn = await eventPage.locator('button').filter({ hasText: /API|Payload/ }).first();
        const visible = await btn.isVisible().catch(() => false);
        results.push({ claim: 20, status: visible ? 'TRUE' : 'FALSE', evidence: `Button visible: ${visible}` });
      } catch {
        results.push({ claim: 20, status: 'FALSE', evidence: 'No button found' });
      }

      // Claim 21: Fee split
      try {
        const fees = await eventPage.locator('[class*="fee"], tr').all();
        results.push({ claim: 21, status: fees.length >= 2 ? 'TRUE' : 'PARTIAL', evidence: `Found ${fees.length} fee items` });
      } catch {
        results.push({ claim: 21, status: 'FALSE', evidence: 'No fees found' });
      }

      // Claim 22: Debate panel
      try {
        const panel = await eventPage.locator('[class*="debate"], [class*="agent"]').first();
        const visible = await panel.isVisible().catch(() => false);
        results.push({ claim: 22, status: visible ? 'PARTIAL' : 'FALSE', evidence: `Panel visible: ${visible}` });
      } catch {
        results.push({ claim: 22, status: 'FALSE', evidence: 'No debate panel found' });
      }

      const filename = eventUrl.split('/').pop();
      await eventPage.screenshot({ path: `${SCREENSHOTS_DIR}/event-${filename}.png` });
      await eventPage.close();
    }

    await eventsPage.close();

  } catch (error) {
    console.error('Script error:', error.message);
  } finally {
    await browser.close();
  }

  // Output results
  console.log('\n===== FACT-CHECK RESULTS =====\n');
  results.forEach((r) => {
    console.log(`${r.claim}. Status: ${r.status} | ${r.evidence}`);
  });

  fs.writeFileSync(`${SCREENSHOTS_DIR}/factcheck-results.json`, JSON.stringify(results, null, 2));
  console.log(`\nResults saved.`);
}

main().catch(console.error);
