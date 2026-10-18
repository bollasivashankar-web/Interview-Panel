# Production deployment

Deployed on 19 October 2026 to the existing Vercel projects in team `sivashankarbollas-3494`.

- Frontend: https://interviewos-web-drab.vercel.app
- API: https://interviewos-api-mu.vercel.app
- Frontend deployment: `dpl_H8Frz9J4vZHGEmUod4HfRmDSRVuj`
- API deployment: `dpl_6E4KrqzdezP74JvUcRu2m3s8KYfK`

Both deployments reached READY. Source was uploaded directly from the checked-in application after deployment by Git reference failed with `git_info_fail`. Automatic deployment on future GitHub pushes has not been confirmed.

The FastAPI build script runs `python -m alembic upgrade head` after dependencies are installed, before deployment. Credentials remain in the existing Vercel project environment settings; no local credential files were uploaded.

Verification: public HTTPS login page, theme/bootstrap and application assets, candidate-to-login redirect, browser console, API health/database readiness, cross-origin response headers, and anonymous API rejection all passed. Browser reported no page errors or failed requests. The built frontend points to the production API.

An authenticated real-user interview, email delivery, and paid AI grading were not exercised during deployment. Existing email-provider settings were retained. Supabase authentication redirect allowlists and provider restrictions should be reviewed if confirmation emails redirect incorrectly or invitations cannot be delivered.
