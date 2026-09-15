# Gardening Companion

**Track plant care schedules, get reminded before your plants suffer, and ask an AI that knows what's in your garden.**

Flask · MongoDB · Celery · AWS S3 · Gemini API · SMTP

---

## The problem

Home gardeners keep plants alive by memory. Care needs vary wildly by species *and* by life stage — seedling basil wants water daily, an established snake plant every three weeks — and that knowledge is scattered across blogs, plant tags and forum threads. The result is the two most common ways houseplants die: **overwatering and neglect.**

Anyone with more than a handful of plants, or plants in more than one place (balcony, indoor, terrace), has nowhere that answers the only question that matters day to day: *what needs attention today?*

## The solution

A multi-tenant web app where users register their plants into named gardens.

- For each plant, a care schedule is derived from a **curated catalog of 40 species**, falling back to the **Gemini API** for anything the catalog doesn't cover — then cached on the species so each one costs at most one API call, ever.
- Intervals are **scaled by life stage**: a seedling is watered twice as often as a mature specimen of the same species.
- From those intervals the app computes per-plant next-due dates and groups the dashboard into **overdue / due today / upcoming**.
- A **Celery Beat** sweep emails each user a **single digest** of everything due — not one email per plant — over **SMTP**.
- A **Gemini-powered chatbot** answers gardening questions with the user's real garden injected as context, so *"what should I do this week?"* gets a real answer.
- Admins curate the shared catalog, review user-submitted requests for missing species, and upload plant photos to **AWS S3**.

---

## Screens

| | |
|---|---|
| **Dashboard** | Stat tiles, a seven-day outlook strip, and plant cards grouped by urgency with one-click Water / Feed buttons |
| **Plant detail** | Full care status, every logged action, and manual schedule overrides |
| **Catalog** | Browse or filter 40 species and add straight into a garden |
| **Ask Sprout** | Chat that knows your plants, your gardens, and what's overdue |
| **Admin** | Catalog curation, the plant-request queue, and user management |

Light and dark themes throughout, responsive to phone width.

---

## Quick start

### With Docker (everything, including a fake inbox)

```bash
cp .env.example .env
# Generate a key:  python -c "import secrets; print(secrets.token_urlsafe(48))"
# and paste it into SECRET_KEY

docker compose up --build -d

docker compose exec web python manage.py ensure-indexes
docker compose exec web python manage.py seed-catalog
docker compose exec web python manage.py create-admin
```

| | |
|---|---|
| App | http://localhost:5000 |
| MailHog (every reminder lands here) | http://localhost:8025 |

The stack runs six services: `web`, `worker`, `beat`, `mongo`, `redis`, `mailhog`.

### Without Docker

Needs a MongoDB you can reach; Redis only if you want background reminders.

```bash
make install            # venv + dependencies
cp .env.example .env    # set SECRET_KEY and MONGO_URI
make seed               # indexes + plant catalog
make admin              # create your admin account
make dev                # http://localhost:5000
```

Reminders, in two more terminals:

```bash
celery -A celery_worker:celery worker --loglevel=info
celery -A celery_worker:celery beat   --loglevel=info
```

### It runs without any API keys

Only `SECRET_KEY` and `MONGO_URI` are required. Everything else degrades cleanly:

| Not configured | What happens |
|---|---|
| `GEMINI_API_KEY` | Curated care data only; the chat widget says so plainly. Nothing else changes. |
| `AWS_S3_BUCKET` | Uploads go to `instance/uploads/` and are served locally. |
| `GOOGLE_CLIENT_ID` | The "Continue with Google" button simply isn't rendered. |
| Redis | The web app is unaffected; only the scheduled sweep stops. Run it by hand with `make reminders`. |

---

## How the core loop works

```
Add plant  ──▶  resolve schedule        curated catalog
                                   ──▶  else Gemini (cached on the species)
                                   ──▶  else conservative default
                     │
                     ▼
              scale by life stage       seedling ×0.5 · young ×0.75 · mature ×1.0
                     │
                     ▼
              next_water_due = (last_watered_at or planted_at) + watering_days
                     │
        ┌────────────┴─────────────┐
        ▼                          ▼
   Dashboard                  Celery Beat (hourly)
   overdue / due / upcoming    find due ▸ claim ▸ one digest email per user
        │                          │
        ▼                          ▼
   "Mark watered"             reminder_log unique index
   ▸ care_logs entry          makes a double-send impossible
   ▸ due date rolls forward
```

**Why the claim step matters.** Before a task goes into an email it's inserted into `reminder_log`, which has a unique index on `(garden_plant_id, action, due_on)`. Only one caller can win that insert — so a retried Celery task, an overlapping sweep, or a second `beat` process cannot email anyone twice about the same due date. If the send then fails, the claims are released and the next sweep retries.

---

## Architecture

```
blueprints/    HTTP only — parse, authorize, call a service, render
services/      business logic: care maths, AI, storage, email
repositories/  every MongoDB query, one module per collection
models/        dataclasses + explicit to_doc / from_doc
```

Business logic never touches `request` and never renders a template, so the care arithmetic is unit-tested directly without a request context.

```
app/
├── __init__.py            application factory
├── config.py              per-environment config, fails fast in production
├── db.py                  Mongo client + every index declared in one place
├── security.py            roles_required, ownership guards, CSP & headers
├── errors.py              typed exceptions ▸ HTML or JSON
├── models/                User, Plant, Garden, GardenPlant, CareLog, PlantRequest
├── repositories/          all data access
├── services/              auth · care · garden · catalog · gemini · storage · email
├── blueprints/            main · auth · dashboard · gardens · plants · chat · profile · admin · api
├── tasks/                 Celery app + the reminder sweep
├── templates/  static/
├── manage.py              ensure-indexes · seed-catalog · create-admin · send-reminders · check
├── celery_worker.py       worker / beat entry point
└── tests/                 164 tests
```

### Data model

| Collection | Holds | Notable index |
|---|---|---|
| `users` | accounts, roles, Google links | unique email / username / phone |
| `plants` | the shared species catalog | unique `scientific_name`, text search |
| `gardens` | named spaces per user | unique `(user_id, name)` |
| `garden_plants` | a planting: species × garden × life stage | `next_water_due`, `next_fertilize_due` |
| `care_logs` | every action ever taken | `(garden_plant_id, created_at)` |
| `plant_requests` | the admin review queue | `(status, created_at)` |
| `chat_messages` | transcripts | TTL, 90 days |
| `reminder_log` | the idempotency ledger | **unique `(garden_plant_id, action, due_on)`** |

`user_id` is an `ObjectId` in every collection — mixing it with strings is exactly how a query silently returns nothing.

---

## Security

- **Every admin route** sits behind a blueprint-wide `before_request` guard, so a new route cannot ship unprotected by accident.
- **CSRF** on every form and on the JSON API (`X-CSRFToken`).
- **Ownership is re-checked server-side** on every mutation; a missing record and someone else's record both return 404, so responses can't be used to probe which ids exist.
- **Password reset tokens are single-use** — the token carries a fingerprint of the current password hash, so using it invalidates it.
- **Login doesn't leak account existence**: a wrong password and an unknown email return the same message.
- **Rate limits** on login, signup, password reset and chat; a per-user daily cap on chat on top of that.
- **Uploads are validated by decoding them** — a renamed script fails at `Image.open` before it can be stored.
- **Security headers** on every response: CSP, `nosniff`, `DENY` framing, referrer policy, HSTS in production.
- **No secrets in the repo.** `.env.example` documents every variable; `.env` is gitignored.

---

## Development

```bash
make test        # pytest (164 tests)
make lint        # ruff
make typecheck   # mypy
make fmt         # ruff --fix + format
make check       # report the status of Mongo, Redis, S3, Gemini, SMTP
make reminders   # dry-run the sweep: prints what each user would receive
```

The suite runs against **mongomock** by default, so `pytest` needs nothing running. Point `TEST_MONGO_URI` at a real MongoDB to exercise the real driver — CI does both.

```bash
TEST_MONGO_URI=mongodb://localhost:27017/ pytest -q
```

### Trying the reminder flow end to end

```bash
python manage.py demo-data --email you@example.com   # plants at varied due dates
python manage.py send-reminders --dry-run            # see what would be sent
python manage.py send-reminders                      # send it — check MailHog
python manage.py send-reminders                      # nothing: already claimed
```

---

## Deployment

`docs/DEPLOY-AWS.md` covers EC2 + nginx + systemd, S3 bucket policy and least-privilege IAM.

The image runs as a non-root user, ships a `/healthz` probe, and reads all configuration from the environment.

```bash
docker build -t gardening-companion .
docker run -p 5000:5000 --env-file .env gardening-companion
```

---

## API

Session-authenticated JSON under `/api/v1` — see `docs/API.md`.

```
GET  /api/v1/me                          account + summary stats
GET  /api/v1/gardens                     gardens with plant counts
GET  /api/v1/gardens/<id>/plants         plantings in a garden
GET  /api/v1/plants/due                  grouped overdue / due / upcoming
GET  /api/v1/plants/<id>                 one planting + its history
POST /api/v1/plants/<id>/care            log a care action
GET  /api/v1/catalog?q=                  search the species catalog
GET  /api/v1/week                        seven-day outlook
```

## License

MIT — see `LICENSE`.
