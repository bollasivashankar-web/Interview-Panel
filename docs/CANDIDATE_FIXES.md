# Candidate page checks — 18 October 2026

## Findings and fixes

- Local API, frontend and PostgreSQL were stopped. Started PostgreSQL using the existing Compose configuration, applied the schema migrations to the newly created local database, and started the API on 127.0.0.1:8000 and frontend on localhost:5173.
- A saved expired/reused invitation forced the candidate route back to an error page with no way to return to existing interviews. Added Back to my interviews, clearing the pending link only on explicit dismissal. Added Retry invitation so transient failures can be retried without signing out.
- Accepting an invitation did not invalidate the candidate session list. Its 15-second fresh cache could show an empty portal immediately after acceptance. Acceptance now invalidates that list before navigation.
- Resumed interviews advanced by array index, so previously saved later questions could be shown again and rejected as duplicate answers. The current question is now derived from the set of unanswered question IDs after every save.

## Verification

The isolated Edge/Playwright browser check in tests/candidate.browser.cjs passed:

1. Expired invitation recovery back to the candidate portal.
2. Retrying acceptance and immediately loading the updated interview list.
3. Camera/microphone check using simulated devices, consent, opening a resumed interview, submitting an answer, skipping an already-saved question, and completing the interview.

No browser page errors occurred. Browser identity and API responses were mocked; this does not verify a real candidate's Supabase account or an external deployment.

Live local readiness and frontend candidate route both returned HTTP 200. The newly initialized local database contains zero companies, invitations and assignments. An actual company invitation must be created and accepted with the candidate's matching email before a real interview appears. No real emails or test invitations were sent.

## Running the browser regression

Start the frontend at http://localhost:5173 with its normal frontend/.env configuration. Run node tests/candidate.browser.cjs with Playwright installed or exposed through NODE_PATH. The script defaults to installed Microsoft Edge; PLAYWRIGHT_CHANNEL can select another installed Playwright browser channel. CANDIDATE_TEST_URL can override the frontend origin.
