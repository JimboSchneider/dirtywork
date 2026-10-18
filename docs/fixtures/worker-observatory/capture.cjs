/* Optional documentation tooling: install Playwright outside the checkout.
 * See the capture recipe beside the Observatory figure in docs/index.html.
 * Uses normal dashboard controls; no DOM, CSS, clock, or API substitutions.
 */
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../../..');
const base = new URL(process.argv[2] || 'http://127.0.0.1:8768/');
const revision = execFileSync('git', ['rev-parse', 'origin/main'], {cwd: root, encoding: 'utf8'}).trim();
const fixtures = fs.readdirSync(path.join(__dirname, 'runs')).sort();

async function capture() {
  const snapshot = await (await fetch(new URL('/api/runs', base))).json();
  assert.deepEqual(snapshot.runs.map(run => run.slug).sort(), fixtures,
    'Point the capture at a disposable copy of the six committed fixture runs.');
  for (const asset of ['index.html', 'app.js', 'style.css', 'presentation.js', 'wopr.js']) {
    const served = Buffer.from(await (await fetch(new URL(asset === 'index.html' ? '/' : '/' + asset, base))).arrayBuffer());
    const expected = execFileSync('git', ['show', `${revision}:dirtywork/dashboard_assets/${asset}`], {cwd: root});
    assert.ok(served.equals(expected), `Serve the dashboard assets from main (${revision}): ${asset}`);
  }

  const browser = await chromium.launch({headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.CHROMIUM_EXECUTABLE_PATH} : {})});
  try {
    for (const appearance of ['light', 'dark']) {
      const context = await browser.newContext({viewport: {width: 1280, height: 1800},
        deviceScaleFactor: 2, colorScheme: appearance, reducedMotion: 'reduce', timezoneId: 'UTC'});
      try {
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base.href);
        await page.locator('#repository').selectOption('repo:/projects/atlas');
        await page.locator('#theme').selectOption('ion');
        await page.locator('#appearance').selectOption(appearance);
        await page.locator('#run-list [data-slug="sample-rounding"]').click();
        await page.locator('#run-task').filter({hasText: 'Fix rounding for invoice totals.'}).waitFor();
        await page.locator('#files-output [role="table"]').waitFor();
        await page.locator('#verify-output').filter({hasText: '12 passed'}).waitFor();
        await page.getByRole('heading', {name: 'Mission control.', exact: true}).click();
        await page.evaluate(() => document.fonts.ready);
        const frame = await page.evaluate(() => {
          const verification = document.getElementById('verify-details').getBoundingClientRect();
          const nextHeading = document.getElementById('timeline-heading').getBoundingClientRect();
          return {width: innerWidth, dpr: devicePixelRatio,
            height: Math.ceil(verification.bottom + 24), next: nextHeading.top,
            overflow: document.documentElement.scrollWidth > innerWidth};
        });
        assert.equal(frame.width, 1280);
        assert.equal(frame.dpr, 2);
        assert.equal(frame.overflow, false);
        assert.ok(frame.height < frame.next, 'End after complete verification evidence, before the next heading.');
        assert.ok(frame.height <= 1800, 'Recompose the frame if the dashboard layout grows.');
        assert.deepEqual(errors, []);
        const output = path.join(root, 'docs/assets', `worker-observatory-${appearance}.png`);
        await page.screenshot({path: output, type: 'png', animations: 'disabled',
          clip: {x: 0, y: 0, width: frame.width, height: frame.height}});
        console.log(`${appearance}: ${frame.width * 2}×${frame.height * 2} PNG from main ${revision}`);
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
}

capture().catch(error => { console.error(error); process.exitCode = 1; });
