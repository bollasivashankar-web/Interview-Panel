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
    let evaluated = false;
    const result = () => ({ id: 'result-1', candidate_name: 'Test Candidate', candidate_email: 'candidate@example.com', job_title: 'Engineer', interview_name: 'Technical round', attempt_number: 1, status: 'completed', started_at: new Date().toISOString(), completed_at: new Date().toISOString(), answered_count: 1, question_count: 1, average_score: evaluated ? 8.5 : null, evaluated_at: evaluated ? new Date().toISOString() : null });
    const detail = () => ({ ...result(), answers: [{ question_id: 'q1', question: 'Explain transaction atomicity.', transcript: 'Atomic transactions either fully commit or roll back.', submitted_at: new Date().toISOString(), score: evaluated ? 8.5 : null, feedback: evaluated ? 'Correct explanation, but an example would improve completeness.' : null }] });
    await page.route('**/api/v1/**', route => {
      const url = new URL(route.request().url());
      const headers = { 'access-control-allow-origin': base, 'access-control-allow-headers': 'authorization,content-type' };
      if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      if (url.pathname === '/api/v1/companies/me') return route.fulfill({ json: { id: 'company-1', name: 'Results Company' }, headers });
      if (url.pathname === '/api/v1/results/result-1/evaluate') { evaluated = true; return route.fulfill({ json: detail(), headers }); }
      if (url.pathname === '/api/v1/results/result-1') return route.fulfill({ json: detail(), headers });
      if (url.pathname === '/api/v1/results') return route.fulfill({ json: [result()], headers });
      return route.fulfill({ json: [], headers });
    });
    if (process.env.TEST_PRODUCTION_DIST) {
      const dist = process.env.TEST_PRODUCTION_DIST;
      const config = JSON.parse(fs.readFileSync(path.join(__dirname, '../frontend/vercel.json'), 'utf8'));
      const headers = Object.fromEntries(config.headers[0].headers.map(header => [header.key, header.value]));
      await page.route(`${base}/company**`, route => route.fulfill({ body: fs.readFileSync(path.join(dist, 'index.html')), contentType: 'text/html', headers }));
      await page.route(`${base}/assets/**`, route => {
        const filename = path.basename(new URL(route.request().url()).pathname);
        return route.fulfill({ body: fs.readFileSync(path.join(dist, 'assets', filename)), contentType: filename.endsWith('.css') ? 'text/css' : 'application/javascript' });
      });
    }
    await page.goto(`${base}/company#results`);
    await page.getByRole('heading', { name: '5. Interview results' }).waitFor();
    await page.getByRole('button', { name: 'View answers' }).click();
    await page.getByRole('button', { name: 'Evaluate answers out of 10' }).click();
    await page.getByText('AI score: 8.5/10', { exact: true }).waitFor();
    await page.getByText('Overall average: 8.5/10', { exact: true }).waitFor();
    await page.getByText('Correct explanation, but an example would improve completeness.', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Answers evaluated', exact: true }).isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS company results, evaluation action, per-question score, feedback, average and saved state');
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
