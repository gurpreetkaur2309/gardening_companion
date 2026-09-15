# Architecture

## Layers

```
blueprints/    HTTP only: parse input, authorize, call a service, render
     │
services/      business logic: care maths, AI, storage, email
     │
repositories/  every MongoDB query, one module per collection
     │
models/        dataclasses with explicit to_doc / from_doc
```

Each layer only calls downward. Services never import `request` or render
templates, which is what lets the care arithmetic be unit-tested with no request
context and no running database.

The obvious alternative — Mongo queries, an API call and a redirect in the same
view function — is how the predecessor to this app was written: one 220-line
`dashboard()` that could not be tested at all without a browser.

## Request lifecycle

```
request
  │  before_request       assign X-Request-ID (logging_config)
  │  before_request       admin blueprint: roles check
  ▼
blueprint view
  │  WTForms              shape validation + CSRF
  │  parse_object_id      malformed id ▸ 404, not 500
  ▼
service
  │  business rules       raises AppError subclasses
  ▼
repository
  │  ownership in the query, not checked afterwards
  ▼
MongoDB
  ▲
  │  errors.py            AppError ▸ HTML page or JSON by content negotiation
  │  after_request        security headers, X-Request-ID
response
```

## Why these choices

**Repositories rather than an ODM.** MongoEngine or Beanie would add a schema
layer, but the queries here are shaped by specific indexes — the due-date sweep,
the stats aggregation — and hand-written queries keep that relationship visible.
Repositories also make the substitution for tests trivial: pass a different
`Database`.

**Dataclasses with explicit serialisation.** `from_doc` / `to_doc` are more
typing than passing dicts around, and they buy two things: every field access is
checked by mypy, and a field rename is a single edit instead of a grep for
string keys across templates and views.

**Schedules stored on the planting, not computed from the species.** A user can
override any interval, and life-stage scaling means two plantings of the same
species legitimately differ. Storing the effective schedule also makes the
reminder sweep a simple indexed range query rather than a join plus arithmetic.

**Cached AI results.** A species' care schedule is written back onto the `plants`
document the first time it's inferred. The hundredth user to add a Monstera pays
nothing, and the catalog improves as the app is used. This is the single biggest
cost lever in the design.

**One digest per user, claimed before sending.** Emailing per plant would make
twenty plants due on one morning into twenty emails. Claiming through a unique
index instead of a "sent" flag means correctness doesn't depend on the sweep
running exactly once — retries and overlapping runs are safe by construction.

## Failure modes, and what happens

| Dependency fails | Behaviour |
|---|---|
| Gemini unavailable, slow, or unconfigured | Care lookups return conservative defaults; chat says so plainly. No 500s, no blocked signups. |
| S3 unreachable | Uploads raise a handled error; existing pages render with the placeholder image. |
| SMTP down | Password reset renders normally (and never reveals whether the address exists); reminder claims are released so the next sweep retries. |
| Redis down | Web app is unaffected. Only the scheduled sweep stops; `manage.py send-reminders` still works. |
| MongoDB down | `/healthz` returns 503 for the load balancer. Pages return a generic error with an incident id. |

Nothing in this table takes the app down as a whole. That's deliberate: a plant
tracker whose dashboard fails because an LLM API is rate-limited is worse than
useless.

## Testing strategy

| Layer | Approach |
|---|---|
| Care arithmetic | Pure unit tests on model methods — no database, no app context |
| Services | Real repositories against mongomock (or real MongoDB via `TEST_MONGO_URI`) |
| Authorization | Every admin route asserted to reject anonymous *and* ordinary users, table-driven so a new route must be added to the table |
| Gemini | A fake client injected into `app.extensions`; covers malformed JSON, out-of-range values and total absence |
| S3 | `moto` for the real boto3 code path |
| Templates | Every GET route rendered, plus all three email templates — catches Jinja errors that no unit test would |

164 tests, and the suite needs nothing running.

## Known limits

- **Timezones.** All scheduling is UTC. A user in UTC+13 sees "due today" flip at
  11:00 local. Per-user timezones would mean storing one and bucketing the sweep
  by it.
- **The sweep is a single pass.** `list_due` caps at 5000 plantings. Past that it
  needs batching or a per-user fan-out into separate tasks.
- **Presigned URL cache is per process.** Each gunicorn worker keeps its own. At
  larger scale this belongs in Redis, or images move behind CloudFront.
- **No email verification on signup.** An account works immediately; the address
  is only proven when a reset link is used.
- **The catalog is global.** A user cannot add a private species — they request
  it and an admin approves. That's a deliberate quality trade-off, not an
  oversight.
