// Screenshot comparison: vanilla vs React
// Usage: node screenshot-compare.mjs
import { chromium } from 'playwright';

const BASE = 'http://localhost:8021';
const PAGES = [
  { name: 'home', vanilla: '/', react: '/app' },
  { name: 'project-graph', vanilla: '/?project=widgets-serializer-refactor&view=graph', react: '/app?project=widgets-serializer-refactor&view=graph' },
  { name: 'project-list', vanilla: '/?project=oss-widgets&view=list', react: '/app?project=oss-widgets&view=list' },
  { name: 'all-prs', vanilla: '/?view=all-prs', react: '/app?view=all-prs' },
  { name: 'all-sessions', vanilla: '/?view=all-sessions', react: '/app?view=all-sessions' },
];

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });

  for (const page of PAGES) {
    console.log(`Capturing: ${page.name}`);

    // Vanilla
    const vanillaPage = await context.newPage();
    await vanillaPage.goto(BASE + page.vanilla, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {});
    await vanillaPage.waitForTimeout(2000); // wait for async renders
    await vanillaPage.screenshot({ path: `/tmp/eva-screenshots/vanilla-${page.name}.png`, fullPage: false });
    await vanillaPage.close();

    // React
    const reactPage = await context.newPage();
    await reactPage.goto(BASE + page.react, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {});
    await reactPage.waitForTimeout(2000);
    await reactPage.screenshot({ path: `/tmp/eva-screenshots/react-${page.name}.png`, fullPage: false });
    await reactPage.close();
  }

  await browser.close();
  console.log('Screenshots saved to /tmp/eva-screenshots/');
}

main().catch(console.error);
