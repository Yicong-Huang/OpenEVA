// E2E comparison: vanilla vs React - every page, every interaction
import { chromium } from 'playwright';

const BASE = 'http://localhost:8021';
const OUT = '/tmp/eva-e2e';

async function screenshot(page, name) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
  console.log(`  [screenshot] ${name}`);
}

async function main() {
  const browser = await chromium.launch();

  for (const [label, prefix] of [['vanilla', ''], ['react', '/app']]) {
    console.log(`\n=== ${label.toUpperCase()} ===`);
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await context.newPage();

    // 1. Initial load
    console.log('1. Initial load');
    await page.goto(BASE + prefix, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3000);
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-01-initial`);

    // 2. Click a project (Example Serializer)
    console.log('2. Click Example project');
    const example = await page.$('text=Example Serializer');
    if (example) {
      await example.click();
      await page.waitForTimeout(500);
      const tracker = await page.$('text=Task Tracker');
      if (tracker) await tracker.click();
      await page.waitForTimeout(2000);
    }
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-02-example-graph`);

    // 3. Click Task Cards tab
    console.log('3. Task Cards view');
    const cardsTab = await page.$('text=Task Cards');
    if (cardsTab) {
      await cardsTab.click();
      await page.waitForTimeout(2000);
    }
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-03-task-cards`);

    // 4. Click Sessions tab
    console.log('4. Sessions view');
    const sessionsTab = await page.$('text=Sessions');
    if (sessionsTab) {
      await sessionsTab.click();
      await page.waitForTimeout(1500);
    }
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-04-sessions`);

    // 5. All Sessions
    console.log('5. All Sessions');
    const allSessions = await page.$('text=All Sessions');
    if (allSessions) {
      await allSessions.click();
      await page.waitForTimeout(1500);
    }
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-05-all-sessions`);

    // 6. All PRs
    console.log('6. All PRs');
    const allPRs = await page.$('text=All PRs');
    if (allPRs) {
      await allPRs.click();
      await page.waitForTimeout(3000);
    }
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-06-all-prs`);

    // 7. Click first PR in list
    console.log('7. Click a PR');
    const prLink = await page.$('.task-card-pr, [data-testid="pr-row"]');
    if (prLink) {
      await prLink.click();
      await page.waitForTimeout(2000);
    }
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-07-pr-detail`);

    // 8. Go to OSS Repo project - Task Cards
    console.log('8. OSS Repo Task Cards');
    const ossRepo = await page.$('text=OSS Example Repo');
    if (ossRepo) {
      await ossRepo.click();
      await page.waitForTimeout(500);
      const tc = await page.$('text=Task Cards');
      if (tc) await tc.click();
      await page.waitForTimeout(2000);
    }
    console.log(`   URL: ${page.url()}`);
    await screenshot(page, `${label}-08-oss-repo-cards`);

    // 9. Click Open Agent on first task (if visible)
    console.log('9. Open Agent button');
    const openAgent = await page.$('text=Open Agent');
    if (openAgent) {
      console.log('   Found Open Agent button');
      // Don't click - just note it exists
    } else {
      console.log('   No Open Agent button visible');
    }
    await screenshot(page, `${label}-09-open-agent`);

    // 10. Top bar interactions
    console.log('10. Top bar');
    await screenshot(page, `${label}-10-topbar`);

    await context.close();
  }

  await browser.close();
  console.log(`\nAll screenshots saved to ${OUT}/`);
}

main().catch(console.error);
