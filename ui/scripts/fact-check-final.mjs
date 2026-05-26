import { chromium } from 'playwright';
import * as fs from 'fs';

const BASE_URL = 'http://localhost:3001';
const SCREENSHOTS_DIR = '/Users/messili/codebase/polyglot-alpha/ui/screenshots/factcheck';

async function main() {
  const browser = await chromium.launch();
  const results = [];

  try {
    // ===== PAGE 1: HOME
    console.log('=== HOME PAGE ===');
    const homePage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await homePage.goto(BASE_URL);
    await homePage.waitForLoadState('networkidle');
    await homePage.waitForTimeout(500);

    // Claim 1: LIVE/MOCK chip
    const buttons = await homePage.locator('button').all();
    let chip1 = false;
    let chipText = 'NOT_FOUND';
    for (let btn of buttons) {
      try {
        const text = await btn.textContent();
        if (text && (text.includes('LIVE') || text.includes('MOCK'))) {
          const vis = await btn.isVisible();
          if (vis) {
            chip1 = true;
            chipText = text;
            break;
          }
        }
      } catch {}
    }
    console.log(`1. LIVE/MOCK chip: ${chip1 ? 'TRUE' : 'FALSE'} - "${chipText}"`);
    results.push({ claim: 1, status: chip1 ? 'TRUE' : 'FALSE', evidence: chipText });

    // Claim 2: 5 flags
    const images = await homePage.locator('img').all();
    let flagCount = 0;
    for (let img of images) {
      const src = await img.getAttribute('src').catch(() => '');
      const alt = await img.getAttribute('alt').catch(() => '');
      if ((src && (src.includes('flag') || src.includes('lang'))) || (alt && (alt.includes('flag') || alt.includes('lang')))) {
        flagCount++;
      }
    }
    console.log(`2. 5 flags: ${flagCount >= 5 ? 'TRUE' : flagCount > 0 ? 'PARTIAL' : 'FALSE'} - found ${flagCount}`);
    results.push({ claim: 2, title: '5 flags in hero', status: flagCount >= 5 ? 'TRUE' : flagCount > 0 ? 'PARTIAL' : 'FALSE', evidence: `${flagCount}` });

    // Claim 3: Hero text
    const h1 = await homePage.locator('h1').first().textContent().catch(() => '');
    const h2 = await homePage.locator('h2').first().textContent().catch(() => '');
    const heroText = h1 || h2 || '';
    const hasHero = heroText.toLowerCase().includes('decentralized') && heroText.toLowerCase().includes('cross-language');
    console.log(`3. Hero "Decentralized cross-language": ${hasHero ? 'TRUE' : 'FALSE'}`);
    results.push({ claim: 3, status: hasHero ? 'TRUE' : 'FALSE', evidence: heroText.substring(0, 50) });

    // Claim 4: Trigger buttons
    let triggerFound = false;
    let triggerText = 'NOT_FOUND';
    for (let btn of buttons) {
      const text = await btn.textContent().catch(() => '');
      if (text && (text.toLowerCase().includes('trigger') || text.toLowerCase().includes('demo'))) {
        triggerFound = true;
        triggerText = text.trim();
        break;
      }
    }
    console.log(`4. Trigger buttons: ${triggerFound ? 'TRUE' : 'FALSE'} - "${triggerText}"`);
    results.push({ claim: 4, status: triggerFound ? 'TRUE' : 'FALSE', evidence: triggerText });

    // Claim 5: Mermaid diagram
    const svgs = await homePage.locator('svg').all();
    console.log(`5. Mermaid diagram: ${svgs.length > 0 ? 'PARTIAL' : 'FALSE'} - ${svgs.length} SVGs`);
    results.push({ claim: 5, status: svgs.length > 0 ? 'PARTIAL' : 'FALSE', evidence: `${svgs.length} SVGs` });

    // Claim 6: 11-node DAG
    const nodes = await homePage.locator('[class*="node"], [data-testid*="node"], div[data-id]').all();
    console.log(`6. 11-node DAG: ${nodes.length === 11 ? 'TRUE' : 'PARTIAL'} - ${nodes.length} nodes`);
    results.push({ claim: 6, status: nodes.length === 11 ? 'TRUE' : 'PARTIAL', evidence: `${nodes.length}/11` });

    // Claim 7: Why now / Problem
    const allHeadings = await homePage.locator('h1, h2, h3, h4, h5').all();
    let hasWhyNow = false;
    for (let h of allHeadings) {
      const text = await h.textContent().catch(() => '');
      if (text && (text.toLowerCase().includes('why') || text.toLowerCase().includes('problem'))) {
        hasWhyNow = true;
        break;
      }
    }
    console.log(`7. "Why now" section: ${hasWhyNow ? 'TRUE' : 'FALSE'}`);
    results.push({ claim: 7, status: hasWhyNow ? 'TRUE' : 'FALSE', evidence: hasWhyNow ? 'Yes' : 'No' });

    // Claim 8: 5 contract addresses
    const bodyText = await homePage.locator('body').textContent().catch(() => '');
    const contractMatches = bodyText.match(/0x[a-fA-F0-9]{40}/g) || [];
    console.log(`8. 5 contracts: ${contractMatches.length >= 5 ? 'TRUE' : contractMatches.length > 0 ? 'PARTIAL' : 'FALSE'} - ${contractMatches.length}`);
    results.push({ claim: 8, status: contractMatches.length >= 5 ? 'TRUE' : contractMatches.length > 0 ? 'PARTIAL' : 'FALSE', evidence: `${contractMatches.length}` });

    await homePage.screenshot({ path: `${SCREENSHOTS_DIR}/home.png` });
    console.log('Screenshot: home.png\n');
    await homePage.close();

    // ===== PAGE 2: ABOUT
    console.log('=== ABOUT PAGE ===');
    const aboutPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await aboutPage.goto(`${BASE_URL}/about`);
    await aboutPage.waitForLoadState('networkidle');
    await aboutPage.waitForTimeout(500);

    const aboutText = await aboutPage.locator('main').textContent().catch(() => '');

    // Claim 9: 5-phase pipeline (not 7)
    const phaseMatches = (aboutText.match(/phase/gi) || []);
    console.log(`9. 5-phase pipeline: Found ${phaseMatches.length} 'phase' mentions`);
    results.push({ claim: 9, status: phaseMatches.length >= 5 ? 'PARTIAL' : 'FALSE', evidence: `${phaseMatches.length} mentions` });

    // Claim 10: How it works section
    const aboutHeadings = await aboutPage.locator('h2, h3, h4').all();
    let hasHowItWorks = false;
    for (let h of aboutHeadings) {
      const text = await h.textContent().catch(() => '');
      if (text && text.toLowerCase().includes('how')) {
        hasHowItWorks = true;
        break;
      }
    }
    console.log(`10. "How it works": ${hasHowItWorks ? 'TRUE' : 'FALSE'}`);
    results.push({ claim: 10, status: hasHowItWorks ? 'TRUE' : 'FALSE', evidence: hasHowItWorks ? 'Yes' : 'No' });

    // Claim 11: 10+1 glossary
    const glossaryElements = await aboutPage.locator('[class*="glossary"], [class*="component"], dl, [class*="definition"]').all();
    console.log(`11. 10+1 glossary: ${glossaryElements.length >= 11 ? 'TRUE' : glossaryElements.length > 0 ? 'PARTIAL' : 'FALSE'} - ${glossaryElements.length}`);
    results.push({ claim: 11, status: glossaryElements.length >= 11 ? 'TRUE' : glossaryElements.length > 0 ? 'PARTIAL' : 'FALSE', evidence: `${glossaryElements.length}` });

    await aboutPage.screenshot({ path: `${SCREENSHOTS_DIR}/about.png` });
    console.log('Screenshot: about.png\n');
    await aboutPage.close();

    // ===== PAGE 3: OPERATORS
    console.log('=== OPERATORS PAGE ===');
    const operatorsPage = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await operatorsPage.goto(`${BASE_URL}/operators`);
    await operatorsPage.waitForLoadState('networkidle');
    await operatorsPage.waitForTimeout(500);

    // Claim 12: 3 seeder cards
    const cards = await operatorsPage.locator('[class*="card"], [role="article"], div[class*="operator"]').all();
    console.log(`12. 3 seeder cards: ${cards.length >= 3 ? 'TRUE' : 'PARTIAL'} - ${cards.length}`);
    results.push({ claim: 12, status: cards.length >= 3 ? 'TRUE' : 'PARTIAL', evidence: `${cards.length}` });

    // Claim 13: wins/bids 26%
    const operatorsText = await operatorsPage.locator('body').textContent().catch(() => '');
    const hasWinsBids = operatorsText.toLowerCase().includes('wins') && operatorsText.toLowerCase().includes('bids');
    const has26 = operatorsText.includes('26');
    console.log(`13. wins/bids 26%: ${hasWinsBids && has26 ? 'TRUE' : hasWinsBids ? 'PARTIAL' : 'FALSE'}`);
    results.push({ claim: 13, status: hasWinsBids && has26 ? 'TRUE' : hasWinsBids ? 'PARTIAL' : 'FALSE', evidence: `wins: ${hasWinsBids}, 26%: ${has26}` });

    // Claim 14: EMA grey dashed
    const hasEMA = operatorsText.toLowerCase().includes('ema');
    console.log(`14. EMA dashed: ${hasEMA ? 'PARTIAL' : 'FALSE'}`);
    results.push({ claim: 14, status: hasEMA ? 'PARTIAL' : 'FALSE', evidence: `EMA found: ${hasEMA}` });

    // Claim 15: Buttons
    const operatorButtons = await operatorsPage.locator('button').all();
    let hasClaimFees = false, hasWithdraw = false, hasRegister = false;
    for (let btn of operatorButtons) {
      const text = await btn.textContent().catch(() => '');
      if (text) {
        if (text.includes('Claim')) hasClaimFees = true;
        if (text.includes('Withdraw')) hasWithdraw = true;
        if (text.includes('Register')) hasRegister = true;
      }
    }
    console.log(`15. Claim/Withdraw/Register buttons: ${hasClaimFees && hasWithdraw && hasRegister ? 'TRUE' : 'PARTIAL'}`);
    results.push({ claim: 15, status: (hasClaimFees && hasWithdraw && hasRegister) ? 'TRUE' : 'PARTIAL', evidence: `Claim: ${hasClaimFees}, Withdraw: ${hasWithdraw}, Register: ${hasRegister}` });

    await operatorsPage.screenshot({ path: `${SCREENSHOTS_DIR}/operators.png` });
    console.log('Screenshot: operators.png\n');
    await operatorsPage.close();

    // ===== PAGE 4: EVENT DETAIL
    console.log('=== EVENT DETAIL (TRYING EVENT 244) ===');
    const eventPage244 = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    try {
      await eventPage244.goto(`${BASE_URL}/events/244`);
      await eventPage244.waitForLoadState('networkidle');
      await eventPage244.waitForTimeout(500);

      const eventText = await eventPage244.locator('body').textContent().catch(() => '');

      // Claim 16: 11-judge dossier
      const tables = await eventPage244.locator('table').all();
      let judgeCount = 0;
      for (let tbl of tables) {
        const rows = await tbl.locator('tbody tr, tr').all();
        if (rows.length > judgeCount) judgeCount = rows.length;
      }
      console.log(`16. 11-judge dossier: ${judgeCount >= 11 ? 'TRUE' : judgeCount > 0 ? 'PARTIAL' : 'FALSE'} - ${judgeCount}`);
      results.push({ claim: 16, status: judgeCount >= 11 ? 'TRUE' : judgeCount > 0 ? 'PARTIAL' : 'FALSE', evidence: `${judgeCount}` });

      // Claim 17: Attestation 0xsim_*
      const hasSimTx = eventText.includes('0xsim');
      console.log(`17. Attestation 0xsim: ${hasSimTx ? 'PARTIAL' : 'FALSE'}`);
      results.push({ claim: 17, status: hasSimTx ? 'PARTIAL' : 'FALSE', evidence: hasSimTx ? 'Found' : 'Not found' });

      // Claim 18: Dossier button
      const eventButtons = await eventPage244.locator('button').all();
      let hasDossierBtn = false;
      for (let btn of eventButtons) {
        const text = await btn.textContent().catch(() => '');
        if (text && (text.toLowerCase().includes('dossier') || text.toLowerCase().includes('raw'))) {
          hasDossierBtn = true;
          break;
        }
      }
      console.log(`18. Dossier button: ${hasDossierBtn ? 'TRUE' : 'FALSE'}`);
      results.push({ claim: 18, status: hasDossierBtn ? 'TRUE' : 'FALSE', evidence: hasDossierBtn ? 'Yes' : 'No' });

      // Claim 19: IPFS ipfs://sim
      const hasIPFS = eventText.includes('ipfs://sim');
      console.log(`19. IPFS ipfs://sim: ${hasIPFS ? 'PARTIAL' : 'FALSE'}`);
      results.push({ claim: 19, status: hasIPFS ? 'PARTIAL' : 'FALSE', evidence: hasIPFS ? 'Found' : 'Not found' });

      // Claim 20: API Payload button
      let hasAPIBtn = false;
      for (let btn of eventButtons) {
        const text = await btn.textContent().catch(() => '');
        if (text && (text.toLowerCase().includes('api') || text.toLowerCase().includes('payload'))) {
          hasAPIBtn = true;
          break;
        }
      }
      console.log(`20. API Payload button: ${hasAPIBtn ? 'TRUE' : 'FALSE'}`);
      results.push({ claim: 20, status: hasAPIBtn ? 'TRUE' : 'FALSE', evidence: hasAPIBtn ? 'Yes' : 'No' });

      // Claim 21: Fee split 2-leg
      const hasFee = eventText.toLowerCase().includes('fee') || eventText.toLowerCase().includes('recipient');
      console.log(`21. 2-leg fee split: ${hasFee ? 'PARTIAL' : 'FALSE'}`);
      results.push({ claim: 21, status: hasFee ? 'PARTIAL' : 'FALSE', evidence: hasFee ? 'Found' : 'Not found' });

      // Claim 22: AgentDebatePanel
      const hasDebate = eventText.toLowerCase().includes('debate') || eventText.toLowerCase().includes('agent');
      console.log(`22. AgentDebatePanel: ${hasDebate ? 'PARTIAL' : 'FALSE'}`);
      results.push({ claim: 22, status: hasDebate ? 'PARTIAL' : 'FALSE', evidence: hasDebate ? 'Found' : 'Not found' });

      await eventPage244.screenshot({ path: `${SCREENSHOTS_DIR}/event-244.png` });
      console.log('Screenshot: event-244.png\n');
    } catch (e) {
      console.log(`Event 244 not found or error: ${e.message}`);
      // Mark all as FALSE since event doesn't exist
      [16, 17, 18, 19, 20, 21, 22].forEach(n => {
        results.push({ claim: n, status: 'FALSE', evidence: 'Event 244 not accessible' });
      });
    }
    await eventPage244.close();

  } catch (error) {
    console.error('Fatal error:', error);
  } finally {
    await browser.close();
  }

  // Save results
  console.log('\n===== SUMMARY =====');
  results.forEach(r => {
    console.log(`${r.claim}. ${r.status}`);
  });

  fs.writeFileSync(`${SCREENSHOTS_DIR}/factcheck-results.json`, JSON.stringify(results, null, 2));
  console.log(`\nSaved to ${SCREENSHOTS_DIR}/factcheck-results.json`);
}

main().catch(console.error);
