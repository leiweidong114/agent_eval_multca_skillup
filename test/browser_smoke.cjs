// Local frontend integration smoke; set NODE_PATH to a directory containing playwright.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const output = path.resolve('.runtime/verification/browser');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const checks = [];
    for (const route of ['/', '/runtimes', '/skills', '/evaluations/new', '/results', '/schematic-overview']) {
      const response = await page.goto((process.env.TEST_FRONTEND_URL || 'http://127.0.0.1:15173') + route);
      await page.waitForLoadState('networkidle', { timeout: 30000 });
      if (!response.ok()) throw new Error(`${route}: ${response.status()}`);
      checks.push({ route, title: await page.title(), status: response.status() });
      if (route === '/results') {
        await page.getByRole('button', { name: '查看', exact: true }).first().click();
        await page.waitForURL('**/results/*/*');
        await page.waitForLoadState('networkidle');
        checks.push({ route: new URL(page.url()).pathname, title: await page.title(), result_detail: true });
      }
    }
    await page.locator('.interaction-card').first().waitFor();
    await page.locator('.raw-log summary').first().click();
    if (!(await page.locator('.raw-log pre').first().innerText()).includes('request_id')) throw new Error('Raw interaction not rendered');
    await page.screenshot({ path: path.join(output, 'schematic-overview.png'), fullPage: false });
    if (await page.getByRole('button', { name: '加载更多交互' }).isVisible()) {
      const count = await page.locator('.interaction-card').count();
      await page.getByRole('button', { name: '加载更多交互' }).click();
      await page.waitForFunction(n => document.querySelectorAll('.interaction-card').length > n, count);
    }
    const report = { checks, errors, interactions: await page.locator('.interaction-card').count() };
    fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    if (errors.length) process.exitCode = 1;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
