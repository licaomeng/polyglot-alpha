import { chromium } from 'playwright';
import * as fs from 'fs';

const BASE_URL = 'http://localhost:3001';
const SCREENSHOTS_DIR = '/Users/messili/codebase/polyglot-alpha/ui/screenshots/factcheck';

async function main() {
  const browser = await chromium.launch();
  const results = [];

  try {
    // ===== PAGE 1: HOME
    console.log('Checking home page...');
    const homePage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await homePage.goto(BASE_URL);
    await homePage.waitForLoadState('networkidle');
    await homePage.waitForTimeout(500);

    // Get all links to understand structure
    const allLinks = await homePage.locator('a').all();
    console.log(`Found ${allLinks.length} links`);
    for (let i = 0; i < Math.min(3, allLinks.length); i++) {
      const href = await allLinks[i].getAttribute('href');
      const text = await allLinks[i].textContent();
      console.log(`  Link ${i}: ${text?.substring(0, 30)} -> ${href}`);
    }

    // Claim 1: LIVE/MOCK chip
    const modeButtons = await homePage.locator('button').all();
    let chip1 = false;
    let chipText = 'NOT_FOUND';
    for (let btn of modeButtons) {
      const text = await btn.textContent().catch(() => '');
      if (text.includes('LIVE') || text.includes('MOCK')) {
        const vis = await btn.isVisible().catch(() => false);
        if (vis) {
          chip1 = true;
          chipText = text;
          break;
        }
      }
    }
    results.push({ claim: 1, status: chip1 ? 'TRUE' : 'FALSE', evidence: `"${chipText}"` });

    // Claim 2: 5 flags
    const allImgs = await homePage.locator('img').all();
    let flagCount = 0;
    for (let img of allImgs) {
      const src = await img.getAttribute('src').catch(() => '');
      const alt = await img.getAttribute('alt').catch(() => '');
      if (src.includes('flag') || alt.includes('flag') || alt.includes('lang')) {
        flagCount++;
      }
    }
    results.push({ claim: 2, status: flagCount >= 5 ? 'TRUE' : flagCount > 0 ? 'PARTIAL' : 'FALSE', evidence: `Found ${flagCount}` });

    // Claim 3: Hero text
    const h1 = await homePage.locator('h1').first().textContent().catch(() => '');
    const h2 = await homePage.locator('h2').first().textContent().catch(() => '');
    const heroText = h1 || h2;
    const hasHero = heroText.toLowerCase().includes('decentralized') && heroText.toLowerCase().includes('cross-language');
    results.push({ claim: 3, status: hasHero ? 'TRUE' : 'FALSE', evidence: `"${heroText.substring(0, 60)}..."` });

    // Claim 4: Trigger buttons
    let triggerFound = false;
    let triggerText = 'NOT_FOUND';
    for (let btn of modeButtons) {
      const text = await btn.textContent().catch(() => '');
      if (text.toLowerCase().includes('trigger') || text.toLowerCase().includes('demo')) {
        const vis = await btn.isVisible().catch(() => false);
        if (vis) {
          triggerFound = true;
          triggerText = text;
          break;
        }
      }
    }
    results.push({ claim: 4, status: triggerFound ? 'TRUE' : 'FALSE', evidence: `"${triggerText}"` });

    // Claim 5: Mermaid
    const svgs = await homePage.locator('svg').all();
    results.push({ claim: 5, status: svgs.length > 0 ? 'PARTIAL' : 'FALSE', evidence: `${svgs.length} SVGs` });

    // Claim 6: DAG 11 nodes
    const nodeElements = await homePage.locator('[class*="node"], [data-testid*="node"]').all();
    results.push({ claim: 6, status: nodeElements.length === 11 ? 'TRUE' : 'PARTIAL', evidence: `${nodeElements.length} nodes` });

    // Claim 7: Why now
    const headings = await homePage.locator('h1, h2, h3, h4').all();
    let hasWhyNow = false;
    for (let h of headings) {
      const text = await h.textContent().catch(() => '');
      if (text.toLowerCase().includes('why') || text.toLowerCase().includes('problem')) {
        hasWhyNow = true;
        break;
      }
    }
    results.push({ claim: 7, status: hasWhyNow ? 'TRUE' : 'FALSE', evidence: hasWhyNow ? 'Found' : 'Not found' });

    // Claim 8: Contract addresses
    const allText = await homePage.locator('body').textContent();
    const contracts = (allText.match(/0x[a-fA-F0-9]{40}/g) || []).length;
    results.push({ claim: 8, status: contracts >= 5 ? 'TRUE' : contracts > 0 ? 'PARTIAL' : 'FALSE', evidence: `${contracts} contracts` });

    await homePage.screenshot({ path: `${SCREENSHOTS_DIR}/home.png` });
    console.log('Home screenshot saved');
    await homePage.close();

    // ===== PAGE 2: ABOUT
    console.log('Checking about page...');
    const aboutPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await aboutPage.goto(`${BASE_URL}/about`);
    await aboutPage.waitForLoadState('networkidle');

    const aboutText = await aboutPage.locator('main').textContent().catch(() => '');
    const phases = (aboutText.match(/phase/gi) || []).length;
    results.push({ claim: 9, status: phases >= 5 ? 'TRUE' : 'FALSE', evidence: `${phases} phase mentions` });

    const aboutHeadings = await aboutPage.locator('h2, h3').all();
    let hasHowItWorks = false;
    for (let h of aboutHeadings) {
      const text = await h.textContent().catch(() => '');
      if (text.toLowerCase().includes('how') || text.toLowerCase().includes('work')) {
        hasHowItWorks = true;
        break;
      }
    }
    results.push({ claim: 10, status: hasHowItWorks ? 'TRUE' : 'FALSE', evidence: hasHowItWorks ? 'Found' : 'Not found' });

    const glossaryItems = await aboutPage.locator('[class*="glossary"], dl, [class*="component"]').all();
    results.push({ claim: 11, status: glossaryItems.length >= 11 ? 'TRUE' : 'PARTIAL', evidence: `${glossaryItems.length} items` });

    await aboutPage.screenshot({ path: `${SCREENSHOTS_DIR}/about.png` });
    console.log('About screenshot saved');
    await aboutPage.close();

    // ===== PAGE 3: OPERATORS
    console.log('Checking operators page...');
    const operatorsPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await operatorsPage.goto(`${BASE_URL}/operators`);
    await operatorsPage.waitForLoadState('networkidle');

    const operatorCards = await operatorsPage.locator('[class*="card"], [role="article"]').all();
    results.push({ claim: 12, status: operatorCards.length >= 3 ? 'TRUE' : 'PARTIAL', evidence: `${operatorCards.length} cards` });

    const operatorText = await operatorsPage.locator('body').textContent();
    const hasWinsBids = operatorText.includes('wins') && operatorText.includes('bids');
    results.push({ claim: 13, status: hasWinsBids ? 'TRUE' : 'FALSE', evidence: hasWinsBids ? 'Found' : 'Not found' });

    const hasEMA = operatorText.toLowerCase().includes('ema') || operatorText.toLowerCase().includes('advanced');
    results.push({ claim: 14, status: hasEMA ? 'PARTIAL' : 'FALSE', evidence: hasEMA ? 'Found EMA' : 'Not found' });

    const operatorButtons = await operatorsPage.locator('button').all();
    let hasClaimFees = false;
    let hasWithdraw = false;
    let hasRegister = false;
    for (let btn of operatorButtons) {
      const text = await btn.textContent().catch(() => '');
      if (text.includes('Claim')) hasClaimFees = true;
      if (text.includes('Withdraw')) hasWithdraw = true;
      if (text.includes('Register')) hasRegister = true;
    }
    results.push({ claim: 15, status: (hasClaimFees && hasWithdraw && hasRegister) ? 'TRUE' : 'PARTIAL', evidence: `Claim: ${hasClaimFees}, Withdraw: ${hasWithdraw}, Register: ${hasRegister}` });

    await operatorsPage.screenshot({ path: `${SCREENSHOTS_DIR}/operators.png` });
    console.log('Operators screenshot saved');
    await operatorsPage.close();

    // ===== PAGE 4: EVENTS & EVENT DETAIL
    console.log('Finding event...');
    const eventsPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await eventsPage.goto(`${BASE_URL}/events`);
    await eventsPage.waitForLoadState('networkidle');

    const eventLinks = await eventsPage.locator('a').all();
    let eventUrl = null;
    for (let link of eventLinks) {
      const href = await link.getAttribute('href').catch(() => '');
      if (href && href.includes('/events/') && href !== '/events') {
        eventUrl = href;
        console.log(`Found event: ${eventUrl}`);
        break;
      }
    }

    if (eventUrl) {
      const fullUrl = eventUrl.startsWith('/') ? BASE_URL + eventUrl : BASE_URL + '/' + eventUrl;
      console.log(`Opening: ${fullUrl}`);
      const eventPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
      await eventPage.goto(fullUrl);
      await eventPage.waitForLoadState('networkidle');
      await eventPage.waitForTimeout(1000);

      const eventText = await eventPage.locator('body').textContent();

      // Judge dossier
      const tables = await eventPage.locator('table').all();
      let judgeCount = 0;
      for (let tbl of tables) {
        const rows = await tbl.locator('tbody tr').all();
        if (rows.length > judgeCount) judgeCount = rows.length;
      }
      results.push({ claim: 16, status: judgeCount >= 11 ? 'TRUE' : judgeCount > 0 ? 'PARTIAL' : 'FALSE', evidence: `${judgeCount} judges` });

      // Attestation tx
      const hasSimTx = eventText.includes('0xsim');
      results.push({ claim: 17, status: hasSimTx ? 'PARTIAL' : 'FALSE', evidence: hasSimTx ? 'Found 0xsim' : 'Not found' });

      // Dossier button
      const btns = await eventPage.locator('button').all();
      let hasDossierBtn = false;
      for (let btn of btns) {
        const text = await btn.textContent().catch(() => '');
        if (text.toLowerCase().includes('dossier') || text.toLowerCase().includes('raw')) {
          hasDossierBtn = true;
          break;
        }
      }
      results.push({ claim: 18, status: hasDossierBtn ? 'TRUE' : 'FALSE', evidence: hasDossierBtn ? 'Found' : 'Not found' });

      // IPFS
      const hasIPFS = eventText.includes('ipfs://sim');
      results.push({ claim: 19, status: hasIPFS ? 'PARTIAL' : 'FALSE', evidence: hasIPFS ? 'Found' : 'Not found' });

      // API Payload button
      let hasAPIBtn = false;
      for (let btn of btns) {
        const text = await btn.textContent().catch(() => '');
        if (text.toLowerCase().includes('api') || text.toLowerCase().includes('payload')) {
          hasAPIBtn = true;
          break;
        }
      }
      results.push({ claim: 20, status: hasAPIBtn ? 'TRUE' : 'FALSE', evidence: hasAPIBtn ? 'Found' : 'Not found' });

      // Fee split
      const hasFee = eventText.includes('fee') || eventText.includes('recipient');
      results.push({ claim: 21, status: hasFee ? 'PARTIAL' : 'FALSE', evidence: hasFee ? 'Found fee terms' : 'Not found' });

      // Debate panel
      const hasDebate = eventText.toLowerCase().includes('debate') || eventText.toLowerCase().includes('agent');
      results.push({ claim: 22, status: hasDebate ? 'PARTIAL' : 'FALSE', evidence: hasDebate ? 'Found debate terms' : 'Not found' });

      const filename = eventUrl.split('/').pop();
      await eventPage.screenshot({ path: `${SCREENSHOTS_DIR}/event-${filename}.png` });
      console.log(`Event ${filename} screenshot saved`);
      await eventPage.close();
    }

    await eventsPage.close();

  } catch (error) {
    console.error('Error:', error);
  } finally {
    await browser.close();
  }

  // Output and save
  console.log('\n===== FACT-CHECK RESULTS =====\n');
  results.forEach((r) => {
    console.log(`${r.claim}. ${r.status} | ${r.evidence}`);
  });

  fs.writeFileSync(`${SCREENSHOTS_DIR}/factcheck-results.json`, JSON.stringify(results, null, 2));
}

main().catch(console.error);
