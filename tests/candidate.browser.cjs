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
    let accepted = false;
    let invitationFails = true;
    let sessionReads = 0;
    let answerSubmissions = 0;
    let answerFails = true;
    const assignment = { id: 'assignment-1', template_id: 'template-1', available_until: new Date(Date.now() + 86400000).toISOString() };
    await page.route('**/api/v1/**', async route => {
      const url = new URL(route.request().url());
      const headers = { 'access-control-allow-origin': base, 'access-control-allow-headers': 'authorization,content-type' };
      if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers });
      if (url.pathname === '/api/v1/invitations/accept') {
        if (invitationFails) return route.fulfill({ status: 410, json: { detail: 'Invitation expired' }, headers });
        accepted = true;
        return route.fulfill({ json: assignment, headers });
      }
      if (url.pathname === '/api/v1/sessions') {
        sessionReads++;
        return route.fulfill({ json: accepted ? [assignment] : [], headers });
      }
      if (url.pathname.endsWith('/attempts')) return route.fulfill({ json: {
        id: 'attempt-1', assignment_id: assignment.id, attempt_number: 1, status: 'active',
        started_at: new Date().toISOString(), deadline_at: new Date(Date.now() + 3600000).toISOString(),
        configuration_snapshot: { duration_minutes: 60, questions: [
          { id: 'q1', text: 'Explain how you test a candidate workflow.', position: 1, time_limit_seconds: 180 },
          { id: 'q2', text: 'This question was already answered.', position: 2, time_limit_seconds: 180 },
        ] }, answered_question_ids: ['q2'],
      }, headers });
      if (url.pathname.endsWith('/answers')) {
        if (answerFails) return route.fulfill({ status: 422, json: { detail: [{ loc: ['body', 'transcript'], msg: 'Please provide a complete answer' }] }, headers });
        answerSubmissions++;
        assert.equal(route.request().postDataJSON().question_id, 'q1');
        return route.fulfill({ status: 201, json: { id: 'answer-1' }, headers });
      }
      if (url.pathname.endsWith('/complete')) return route.fulfill({ json: { status: 'completed' }, headers });
      return route.fulfill({ status: 404, json: { detail: 'Unexpected test route' }, headers });
    });

    await page.goto(`${base}/candidate`);
    await page.getByRole('heading', { name: 'Your first interview starts with an invitation' }).waitFor();
    await page.evaluate(() => localStorage.setItem('invite_token', 'expired-browser-test-token-123456789'));
    await page.reload();
    await page.getByText('Invitation expired', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Back to my interviews' }).click();
    await page.getByRole('heading', { name: 'Your interviews', exact: true }).waitFor();
    await page.getByRole('link', { name: 'InterviewOS home', exact: true }).click();
    await page.getByRole('heading', { name: 'Your interviews', exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, '/candidate', 'Candidate logo must stay in the candidate portal');
    assert.equal(await page.evaluate(() => localStorage.getItem('invite_token')), null);
    console.log('PASS expired invitation recovery');

    await page.goto(`${base}/invite#token=new-browser-test-token-123456789`);
    await page.getByText('Invitation expired', { exact: true }).waitFor();
    invitationFails = false;
    const beforeAcceptance = sessionReads;
    await page.getByRole('button', { name: 'Retry invitation' }).click();
    await page.getByRole('button', { name: 'Start or resume' }).waitFor();
    assert.ok(sessionReads > beforeAcceptance, 'Acceptance must refetch the cached session list');
    console.log('PASS invitation retry and immediate interview visibility');

    await page.getByRole('button', { name: 'Start or resume' }).click();
    await page.getByRole('heading', { name: 'Before you begin' }).waitFor();
    await page.locator('.desktop-tabs').getByRole('link', { name: 'My interviews', exact: true }).click();
    await page.getByRole('heading', { name: 'Your interviews', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Start or resume' }).click();
    await page.getByRole('heading', { name: 'Before you begin' }).waitFor();
    await page.getByRole('button', { name: 'Check camera and microphone' }).click();
    await page.getByRole('button', { name: 'Check again', exact: true }).waitFor();
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: 'Begin interview', exact: true }).click();
    await page.getByRole('heading', { name: 'Explain how you test a candidate workflow.' }).waitFor();
    await page.getByRole('textbox').fill('I verify the candidate page, API and saved answers.');
    await page.getByRole('button', { name: 'Submit and continue' }).click();
    await page.getByRole('alert').filter({ hasText: 'transcript: Please provide a complete answer' }).waitFor();
    assert.equal(await page.getByRole('textbox').inputValue(), 'I verify the candidate page, API and saved answers.', 'A rejected answer must remain editable');
    answerFails = false;
    await page.getByRole('button', { name: 'Submit and continue' }).click();
    await page.getByRole('heading', { name: 'All answers are saved' }).waitFor();
    assert.equal(answerSubmissions, 1);
    await page.getByRole('button', { name: 'Complete interview', exact: true }).click();
    await page.getByRole('heading', { name: 'Interview submitted' }).waitFor();
    assert.deepEqual(errors, []);
    console.log('PASS device check, resume, skip saved questions, answer and completion');
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
