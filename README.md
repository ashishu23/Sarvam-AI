# Eagle Eye ? GitHub ? Vercel

The repository includes a React frontend and FastAPI API in **one Vercel project**. Import the repository root, not the frontend subfolder. The Vercel configuration sets the install/build/output commands and API routing automatically.

## One-time setup

Persistent private cases require a Supabase project. Connecting GitHub alone cannot create your database or supply secret API credentials.

1. Create a Supabase project. Open its SQL Editor and run [001_eagle_eye.sql](./supabase/migrations/001_eagle_eye.sql). This creates cases, revision transactions, quota counters and the private document bucket with ownership policies. It is safe to run again.
2. In Supabase Authentication, enable **Email** sign-in. Anonymous sign-in is not required. After Vercel assigns your URL, set that URL as the Supabase **Site URL** and add it to the redirect allowlist so confirmation emails return to the app.
3. Push the repository to GitHub. Keep secrets, uploads and dependency folders excluded by [.gitignore](./.gitignore).
4. Import it in Vercel with **Root Directory: repository root**, **Framework Preset: Other**, and **Node.js: 22.x**. Keep the commands/output from [vercel.json](./vercel.json). Enable Fluid compute if your project has that option.
5. Before Deploy, add these environment variables for the deployment environments you use:

| Variable | Value |
|---|---|
| `SARVAM_API_KEY` | Your real Sarvam API key, entered in Vercel settings only |
| `SUPABASE_URL` | The HTTPS project URL from Supabase |
| `SUPABASE_PUBLISHABLE_KEY` | The publishable key (or legacy anon key), never the service-role/secret key |

No `VITE_*` variables are required on Vercel. The frontend reads public Supabase configuration from the API; the Sarvam secret stays on the server. The entrypoint forces production and Supabase mode on Vercel. There is no local-filesystem fallback on a hosted deployment.

6. Deploy, configure the Supabase authentication URLs, then create an account in the app and confirm its email. Sign in and start a case. The sample needs no account or model calls.

## Verify your deployment

- Open `/healthz`: expect `status: ok`.
- Open `/readyz`: expect `storage_mode: supabase`, `ai_configured: true` and `ai_enabled: true`. This endpoint does not spend model credits or prove the migration was applied.
- Create/sign in to an account, create a case, reload, and verify it appears under My cases.
- Upload a synthetic document, click Check extraction, review the extracted text, confirm it, then Review terms or ask a question.
- Check another account cannot open that case. Delete it and check the original is no longer available after completed cleanup.

## Deployment limits and privacy

Uploads are limited to **3 MB total per batch**, 3 MB per file, and 10 pages. Supported formats: JPEG, PNG, WebP and PDF. These smaller limits keep requests below Vercel's 4.5 MB function payload limit. Send large documents in separate, clearly partial batches. A case's JSON history also has a bounded size; export and start a new case if it fills up.

Case data and originals persist in Supabase; Sarvam processes AI requests. Recordings are discarded after transcription. No service-role key is used for ordinary requests. API responses are not cached. Deletion first blocks application access, then removes stored files and clears the case record. A failed cleanup must be retried; the app does not claim control over vendor backups. Storage policies allow the owner to access pending-cleanup files until their actual deletion because the Storage delete operation requires select permission.

Long document jobs retain the provider job ID and resume through Check extraction. Nothing depends on an in-process background worker. If submission is interrupted before the provider ID is stored, the app reports that uncertainty; inspect provider usage before resubmitting. Model/account access and credits are still required.

## Local development

Python 3.13 and Node 22.12+ are used. Preserve the existing root `.env`. Install dependencies on a new machine:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
npm.cmd ci --prefix frontend
```

Terminal 1, from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Terminal 2:

```powershell
npm.cmd run dev --prefix frontend
```

Open http://localhost:5173. Default local mode stores private records in ignored `private-data/` with separate opaque browser sessions. It is for loopback development only. Clearing local browser storage loses access to that session. Set `STORAGE_MODE=supabase` and the Supabase variables locally to test the cloud sign-in path.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend api
npm.cmd run lint --prefix frontend
npm.cmd run test --prefix frontend
npm.cmd run build --prefix frontend
```

CI runs these checks without real Sarvam credentials. The migration test uses an isolated embedded PostgreSQL instance with minimal Auth/Storage schema fixtures; it is not a test of your hosted Supabase account. Patched PDF.js and test dependencies replaced the earlier vulnerable versions; dependency installation reported zero npm vulnerabilities.

## Scope and verification boundaries

This prepares the existing basic application for deployment; it does not claim every item in [Ideation1](./Ideation1.md) and [Ideation2](./Ideation2.md) is complete. The basic UI covers accounts, cases, page upload/order/rotation, extraction confirmation, role-based review, questions, voice transcription, drafts, export and deletion. Full Hindi interface coverage, comparison/reconciliation screens, playback/translation controls and polished printable briefs remain future UI work; some corresponding API operations already exist.

The real Sarvam conversational smoke call passed. No real Supabase project or Vercel deployment has been created or tested in this workspace. Live OCR, speech and translation still require full integration verification. No professional legal review has occurred, and no substantive employment-law source pack is admitted. The application explains documents and helps organise factual communications without declaring enforceability or signing safety.

## Hosting references

- [Vercel Python API functions](https://vercel.com/docs/functions/runtimes/python/api-directory)
- [Vercel function limits](https://vercel.com/docs/functions/limitations)
- [Supabase row-level security](https://supabase.com/docs/guides/database/postgres/row-level-security)
