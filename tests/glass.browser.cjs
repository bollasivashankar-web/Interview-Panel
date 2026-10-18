// Run with NODE_PATH pointing to an installed Playwright package.
// Uses an isolated browser and mocked identity/API responses; no real emails or accounts.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const base = process.env.CANDIDATE_TEST_URL || 'http://localhost:5173';
  const env = fs.readFileSync(path.join(__dirname, '../frontend/.env'), 'utf8');
  const authUrl = env.match(/^VITE_SUPABASE_URL\s*=\s*["']?([^\s"']+)/m)?.[1];
  assert.ok(authUrl, 'Frontend Supabase URL must be configured');
  const storageKey = `sb-${new URL(authUrl).hostname.split('.')[0]}-auth-token`;
  const expires = Math.floor(Date.now() / 1000) + 3600;
  const payload = Buffer.from(JSON.stringify({ sub: 'browser-candidate', exp: expires, aud: 'authenticated' })).toString('base64url');
  const session = { access_token: `eyJhbGciOiJIUzI1NiJ9.${payload}.test`, refresh_token: 'browser-test', expires_at: expires, expires_in: 3600, token_type: 'bearer', user: { id: 'browser-candidate', email: 'candidate@example.com', aud: 'authenticated', app_metadata: {}, user_metadata: {}, created_at: new Date().toISOString() } };
  const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true, args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] });
  const context = await browser.newContext({ permissions: ['camera', 'microphone'] });
  try {
    await context.addInitScript(({ key, value }) => localStorage.setItem(key, JSON.stringify(value)), { key: storageKey, value: session });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/auth/v1/**', route => route.fulfill({ json: session.user }));
    await page.emulateMedia({ colorScheme: 'light' });
    const company = { id: 'company-1', name: 'Acme Studio' };
    const job = { id: 'job-1', title: 'Product Engineer', description: 'Build thoughtful software with a small and collaborative team.', required_skills: ['React', 'TypeScript'] };
    const interview = { id: 'template-1', job_id: job.id, name: 'Product engineering conversation', duration_minutes: 30, attempt_limit: 1, published: true };
    const result = { id: 'result-1', candidate_name: 'Alex Morgan', candidate_email: 'alex@example.com', job_title: job.title, interview_name: interview.name, attempt_number: 1, status: 'completed', started_at: new Date().toISOString(), completed_at: new Date().toISOString(), answered_count: 1, question_count: 1, average_score: 8.5, evaluated_at: new Date().toISOString() };
    await page.route('**/api/v1/**', route => {
      const url = new URL(route.request().url());
      const headers = { 'access-control-allow-origin': base, 'access-control-allow-headers': 'authorization,content-type' };
      if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      const values = { '/api/v1/companies/me': company, '/api/v1/jobs': [job], '/api/v1/interviews': [interview], '/api/v1/results': [result], '/api/v1/sessions': [{ id: 'assignment-1', template_id: interview.id, available_until: new Date(Date.now() + 86400000).toISOString() }] };
      if (url.pathname.includes('/questions')) return route.fulfill({ json: [{ id: 'q1', template_id: interview.id, text: 'How do you bring clarity to a complex product problem?', position: 1, time_limit_seconds: 180, approved: true, rubric: { competency: 'Product thinking' } }], headers });
      return route.fulfill({ json: values[url.pathname] || [], headers });
    });
    const dir = path.join(__dirname, '../artifacts/liquid-glass'); fs.mkdirSync(dir, { recursive: true });
    const accessibility = [];
    async function audit(label) {
      if (!process.env.GLASS_AXE) return;
      await page.evaluate(async () => Promise.all(document.getAnimations().filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {}))));
      await page.addScriptTag({ content: fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8') });
      const report = await page.evaluate(async () => {
        const result = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] } });
        return { violations: result.violations.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })), incomplete: result.incomplete.map(item => item.id) };
      });
      accessibility.push({ label, ...report });
      fs.writeFileSync(path.join(dir, 'accessibility.json'), JSON.stringify(accessibility, null, 2));
      assert.deepEqual(report.violations, [], `Accessibility violations: ${label}`);
    }
    async function checkViewport(width, height) {
      await page.setViewportSize({ width, height });
      await page.evaluate(() => document.fonts.ready);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Horizontal overflow at ${width}px`);
    }
    await page.setViewportSize({ width: 1280, height: 960 });
    await page.goto(`${base}/company`);
    await page.getByRole('heading', { name: 'Acme Studio', exact: true }).waitFor();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    await audit('company light desktop');
    await page.screenshot({ path: path.join(dir, 'company-light.png'), fullPage: true, animations: "disabled" });
    await page.getByRole('button', { name: 'Appearance: system. Switch to light', exact: true }).click();
    await page.getByRole('button', { name: 'Appearance: light. Switch to dark', exact: true }).click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.reload();
    await page.getByRole('heading', { name: 'Acme Studio', exact: true }).waitFor();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await audit('company dark desktop');
    await page.screenshot({ path: path.join(dir, 'company-dark.png'), fullPage: true, animations: "disabled" });
    for (const width of [768, 390, 320]) await checkViewport(width, 844);
    await page.setViewportSize({ width: 390, height: 844 });
    const nav = await page.locator('.mobile-tabs').boundingBox();
    assert.ok(nav && nav.y > 700 && nav.y + nav.height <= 844, 'Mobile navigation must stay at the viewport bottom');
    await page.screenshot({ path: path.join(dir, 'company-mobile.png'), fullPage: true, animations: "disabled" });
    await page.goto(`${base}/candidate`);
    await page.getByRole('heading', { name: 'Your interviews', exact: true }).waitFor();
    await audit('candidate dark mobile');
    assert.equal(await page.locator('nav a[href="/company"], nav a[href="/company#results"]').count(), 0, 'Candidate navigation must hide company workspace and results');
    await page.locator('.mobile-tabs').getByRole('link', { name: 'My interviews', exact: true }).waitFor();
    await page.screenshot({ path: path.join(dir, 'candidate-mobile.png'), fullPage: true, animations: "disabled" });
    await page.getByRole('button', { name: 'Appearance: dark. Switch to system', exact: true }).click();
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior), 'auto');
    await page.goto(`${base}/login`);
    await page.getByRole('heading', { name: 'Welcome back', exact: true }).waitFor();
    await audit('login light mobile');
    await page.screenshot({ path: path.join(dir, 'login-mobile.png'), fullPage: true, animations: "disabled" });
    await checkViewport(1280, 960);
    await page.screenshot({ path: path.join(dir, 'login-light.png'), fullPage: true, animations: "disabled" });
    const themeControl = page.getByRole('button', { name: 'Appearance: system. Switch to light', exact: true });
    await themeControl.focus();
    assert.equal(await themeControl.evaluate(element => getComputedStyle(element).outlineStyle), 'solid');
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.documentElement.dataset.themePreference === 'light');
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.type), 'email');
    assert.deepEqual(errors, []);
    console.log('PASS themes, persistence, system changes, 320/390/768/1280 layouts, bottom navigation, reduced motion and page renders');
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
