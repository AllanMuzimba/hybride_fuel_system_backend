# Deploy with Render and Supabase

The backend and Flutter frontend live in separate GitHub repositories. The
`render.yaml` Blueprint in this backend repository creates a free Render web
service for FastAPI and a free Render static site for the Flutter web build.
Render deploys both from GitHub; the Render CLI is optional.

Use Supabase Postgres for data you intend to keep. Render's free Postgres
database expires after 30 days. Supabase Free has a 500 MB database limit and
can pause after a period of low activity, so arrange independent backups and
consider a paid plan for a real station.

## 1. Create the Postgres database

Create a Supabase project and copy its **Session pooler** connection string
(port 5432) from **Connect**. The Render service needs the database password.
Keep the connection string secret. Use the SQLAlchemy form for `DATABASE_URL`:

```text
postgresql+psycopg2://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
```

If the password contains special URL characters, URL-encode them. The direct
Supabase connection requires IPv6; the session pooler works for Render's
long-running Python process.

From this repository, initialize a **new, empty** database with the full
schema, then create the first account interactively:

```bash
psql 'postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require' -v ON_ERROR_STOP=1 -f schema.sql
DATABASE_URL='postgresql+psycopg2://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require' python create_first_company.py
```

Use your actual connection details without committing them. `schema.sql`
already contains the offline transaction request IDs. For an **existing**
database, apply pending SQL files in `migrations/` in order instead of running
the full schema again. Do not use `seed_demo.py` for a live installation.

## 2. Deploy the GitHub repositories

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
   | API | `DATABASE_URL` | Supabase session pooler SQLAlchemy URL from step 1 |
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

In the **backend** GitHub repository, add the Actions variable
`RENDER_HEALTH_URL` with this value:

```text
https://YOUR-API.onrender.com/health?check_database=true
```

The scheduled workflow in `.github/workflows/render-keepalive.yml` pings it
every ten minutes and can be run manually in GitHub Actions. Without the
variable, the job skips. GitHub scheduled jobs can be delayed or missed, and
GitHub may disable schedules in inactive public repositories; the ping does
not guarantee zero cold starts. Render's free web service sleeps after 15
minutes without traffic and has a monthly free instance-hour allowance. The
database check also generates periodic activity for Supabase, but it is not
a substitute for backups or a paid always-on service.

## Render CLI (optional)

The GitHub Blueprint handles builds and deploys; no CLI is needed on Render.
If you install the official Render CLI and log in, it can validate
`render.yaml` and inspect services. Keep API keys and database credentials out
of the repositories.
