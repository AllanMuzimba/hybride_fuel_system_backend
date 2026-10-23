# Deploy with Render and Layerbase

The backend and Flutter frontend live in separate GitHub repositories. Both
repositories are connected to Render and deploy automatically from GitHub:

| Service | Live URL | Source branch |
| --- | --- | --- |
| FastAPI (`hybrid-fuel-api`) | https://hybrid-fuel-api.onrender.com | `hybride_fuel_system_backend:backend-main` |
| Flutter web (`hybrid-fuel-web`) | https://hybrid-fuel-web.onrender.com | `hybride_fuel_system:main` |

The services were created with the Render CLI. The `render.yaml` Blueprint is
kept as a reproducible configuration for a fresh workspace; do not create it
again in this workspace, because that would make duplicate services.

The `hybrid_fuel` database already exists on Layerbase Cloud in its US East
region, running stable PostgreSQL 18. Its full `schema.sql` was applied on
2026-10-23. The Render API is set to Virginia, close to that database. The free Layerbase plan has no trial expiry, but idle databases
sleep and can eventually be archived. Free has one manual backup slot and no
scheduled backups, so arrange independent exports. Layerbase describes Free
as an option for development and early-stage products, with paid plans for
products earning recurring production revenue.

## 1. Connect to the Postgres database

Open `hybrid_fuel` in Layerbase Cloud and copy the **pooled PostgreSQL**
connection string from Quick Connect. Keep it secret. The API's `DATABASE_URL`
uses the SQLAlchemy driver prefix and requires TLS:

```text
postgresql+psycopg2://USER:PASSWORD@POOLER_HOST/hybrid_fuel?sslmode=require
```

Replace only `postgresql://` in Layerbase's URL with
`postgresql+psycopg2://`; retain its real host, credentials, database, and
query string. If a password is written into a URL by hand, URL-encode special
characters. Use the **direct** connection string for schema migrations and
exports. The application uses the pooled URL and limits its own connection
pool to leave room under Layerbase Free's 20-connection limit.

The schema and first `Hybrid Fuel` company, `Main Branch`, and `fuel_admin`
developer login are already initialized. For a new installation on an empty
database, run this script interactively with the private database URL set in
your environment or a local `.env` file (which Git ignores):

```bash
python create_first_company.py
```

For a new, empty database only, apply `schema.sql` with a PostgreSQL client
using Layerbase's direct URL. The schema already contains the offline
transaction request IDs. For an existing database, apply pending SQL files in
`migrations/` in order, including `011_accounts_billing.sql`. Do not run the full schema twice or use
`seed_demo.py` for a live installation.

The developer dashboard manages companies, company applications, all users,
and manual company invoices. Invoice status records whether an invoice is
unpaid, paid, or void; the app does not process payments.

The API accepts username, email, or phone at sign-in. Users can edit their
own email and phone and change their password after entering the current
password. Only a company director can change roles within their company;
developer and dev-support accounts can manage company roles, while only the
developer can assign or manage platform roles.

Password reset links use the Flutter web app's `?reset_token=` parameter and
expire after 30 minutes. To enable reset email delivery, set these API service
environment variables in Render. For Gmail, use an app password in
`SMTP_PASSWORD` instead of the account's regular sign-in password.

| Variable | Value |
| --- | --- |
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | `587` for STARTTLS, or `465` for implicit TLS |
| `SMTP_USERNAME` | SMTP account username |
| `SMTP_PASSWORD` | SMTP app password or provider credential |
| `SMTP_FROM_EMAIL` | Sender address (defaults to `SMTP_USERNAME`) |
| `PASSWORD_RESET_URL` | `https://hybrid-fuel-web.onrender.com/` |

The forgot-password screen is available before sign-in. Until SMTP is
configured, the API reports that password reset email is unavailable.

## 2. Deploy the GitHub repositories in a fresh workspace

1. In Render, connect both GitHub repositories:
   `AllanMuzimba/hybride_fuel_system_backend` and
   `AllanMuzimba/hybride_fuel_system`.
2. Create a **Blueprint** from the backend repository's `backend-main` branch.
   Render reads `render.yaml` and creates `hybrid-fuel-api` and
   `hybrid-fuel-web`. If these names are taken, Render may choose different
   public URLs; use the actual URLs shown on the service pages.
3. When the Blueprint prompts for environment variables, set:

   | Service | Variable | Value |
   | --- | --- | --- |
   | API | `DATABASE_URL` | Layerbase pooled SQLAlchemy URL from step 1 |
   | API | `CORS_ORIGINS` | Exact web origin, e.g. `https://hybrid-fuel-web.onrender.com`, without trailing slash |
   | Web | `API_BASE_URL` | Exact API origin, e.g. `https://hybrid-fuel-api.onrender.com`, without trailing slash |

   Render generates `JWT_SECRET_KEY`. Keep it stable so existing login tokens
   remain valid. Once Render assigns the actual URLs, correct either origin
   setting if necessary and redeploy the affected service. Flutter compiles
   `API_BASE_URL` into the web build, so changing it needs a web redeploy.
4. Open `https://YOUR-API.onrender.com/health?check_database=true` and the
   static site. The health endpoint returns 503 if the API cannot reach
   Postgres. An online login verifies the database and browser CORS settings.

The frontend build script clones Flutter 3.47.5 on Render, runs `flutter pub
get`, and builds the static web output. To use an existing local Flutter SDK,
set `FLUTTER_SDK=/path/to/flutter` when running the script locally.

For an installed mobile app, build with the same public API address:

```bash
flutter build apk --dart-define=API_BASE_URL=https://YOUR-API.onrender.com
```

## 3. External health checks

In the **backend** GitHub repository, set the Actions variable
`RENDER_HEALTH_URL` to the deployed API health URL:

```text
https://YOUR-API.onrender.com/health?check_database=true
```

Also set the Actions **secret** `LAYERBASE_CONNECTION_STRING` to Layerbase's
PostgreSQL connection URL, including `sslmode=require`. The secret stays in
GitHub Actions settings and must never be committed to this repository.

The workflow in `.github/workflows/render-keepalive.yml` queries Layerbase
directly every ten minutes. It checks the Render API once daily at 05:07 UTC
and on manual dispatch. The Layerbase query does not wake Render. A separate
external monitoring on cron-job.org uses two jobs:

| Title | URL | Schedule |
| --- | --- | --- |
| `Hybrid Fuel Render keep-alive` | `https://hybrid-fuel-api.onrender.com/ping` | Every 10 minutes |
| `Hybrid Fuel Layerbase database` | `https://hybrid-fuel-api.onrender.com/health?check_database=true` | Every 10 minutes |

The cron-job.org jobs are enabled in the owner's account as jobs `8705779`
and `8706922`. They are independent of GitHub Actions. The database job calls
the database-aware health route, so HTTP 200 means Render answered and
Layerbase completed a real query. They are not configured inside the Python
process. The API key used to manage
the job is stored locally at `.local-secrets/cron-job-api-key` in the frontend
workspace, outside Flutter assets and Git. `/ping` remains available as a
simple probe, but `/health?check_database=true` also verifies Layerbase.

Any external request, including GitHub and cron-job.org, wakes a sleeping
Render web service. The ten-minute cron-job.org schedule is intended to keep
the API warm and continuously verify its Layerbase connection.
GitHub scheduled jobs can be delayed or missed and may be disabled in an
inactive public repository. The checks are not a substitute for backups or
paid availability.

## Render CLI (optional)

The current services were created with the official Render CLI and connected
to their GitHub repositories. The CLI can also validate `render.yaml` and
inspect services. Keep API keys and database credentials out of the
repositories.
