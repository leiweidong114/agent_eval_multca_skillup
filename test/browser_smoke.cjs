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
    const baseUrl = process.env.TEST_FRONTEND_URL || 'http://127.0.0.1:5173';
    await page.goto(baseUrl + '/');
    if (await page.getByRole('heading', { name: '登录评测平台' }).count()) {
      await page.getByLabel('工号').fill(process.env.TEST_EMPLOYEE_NO || 'browser-smoke');
      await page.getByLabel('密码').fill(process.env.TEST_PASSWORD || 'local-smoke-only');
      await page.getByRole('button', { name: '登录' }).click();
      await page.getByText('AGENT EVAL', { exact: true }).waitFor({ timeout: 30000 });
      checks.push({ route: '/login', authenticated: true });
    }
    for (const route of ['/', '/runtimes', '/skills', '/evaluations/new', '/results', '/schematic-overview']) {
      const response = await page.goto(baseUrl + route);
      await page.waitForLoadState('networkidle', { timeout: 30000 });
      if (!response.ok()) throw new Error(`${route}: ${response.status()}`);
      checks.push({ route, title: await page.title(), status: response.status() });
      if (route === '/results') {
        const details = page.getByRole('button', { name: /^(查看|实时查看)$/ });
        if (!(await details.count())) throw new Error('Authenticated results list is empty');
        await details.first().click();
        await page.waitForURL('**/results/*/*');
        await page.waitForLoadState('networkidle');
        checks.push({ route: new URL(page.url()).pathname, title: await page.title(), result_detail: true });
      }
    }
    const conversations = page.locator('.conversation-row');
    if (!(await conversations.count())) throw new Error('Schematic conversation list is empty');
    await conversations.first().click();
    await page.locator('.conversation-dialog').waitFor();
    await page.locator('.turn-list .turn-card').first().waitFor();
    await page.locator('.turn-list .turn-card').first().click();
    await page.locator('.interaction-detail-dialog').waitFor();
    await page.getByText('Request & Response', { exact: true }).waitFor();
    const interactionText = await page.locator('.interaction-detail-dialog').innerText();
    if (!interactionText.includes('Input') || !interactionText.includes('Output')) {
      throw new Error('Structured Input/Output interaction detail not rendered');
    }
    await page.screenshot({ path: path.join(output, 'schematic-overview.png'), fullPage: false });
    const report = { checks, errors, interactions: await page.locator('.turn-list .turn-card').count() };
    fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    if (errors.length) process.exitCode = 1;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
