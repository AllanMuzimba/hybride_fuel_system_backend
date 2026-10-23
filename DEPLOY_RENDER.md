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
`migrations/` in order. Do not run the full schema twice or use
`seed_demo.py` for a live installation.

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

## 3. GitHub keepalive

In the **backend** GitHub repository, set the Actions variable
`RENDER_HEALTH_URL` to the deployed API health URL:

```text
https://YOUR-API.onrender.com/health?check_database=true
```

Also set the Actions **secret** `LAYERBASE_CONNECTION_STRING` to Layerbase's
PostgreSQL connection URL, including `sslmode=require`. The secret stays in
GitHub Actions settings and must never be committed to this repository.

The scheduled workflow in `.github/workflows/render-keepalive.yml` runs every
ten minutes and can also be started manually. One step requests the Render API
health endpoint; another runs `SELECT 1` directly on Layerbase. Without the
Render URL variable, the Render step skips. The Layerbase step fails if its
secret is missing. GitHub scheduled jobs can be delayed or missed, and GitHub
may disable schedules in inactive public repositories. This does not
guarantee zero cold starts. Render's free web service sleeps after 15 minutes
without traffic and has a monthly free instance-hour allowance. The checks
are not a substitute for backups or paid availability.

While the FastAPI process runs on Render, a background task also calls its
public `/health?check_database=true` URL every ten minutes after an initial
60-second delay. Render provides that URL through `RENDER_EXTERNAL_URL`.
`/ping` is available as a simple external probe. An in-process task cannot
wake the service after Render has already stopped it, so the GitHub workflow
remains the external wake-up check.

## Render CLI (optional)

The current services were created with the official Render CLI and connected
to their GitHub repositories. The CLI can also validate `render.yaml` and
inspect services. Keep API keys and database credentials out of the
repositories.
