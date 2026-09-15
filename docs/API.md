# JSON API (v1)

Session-authenticated: sign in through the web app and the browser's cookie
authenticates API calls too. There are no API tokens — this exists for the app's
own front end and for a future mobile client sharing the same session.

**Base path:** `/api/v1`

### Authentication

Every endpoint requires a signed-in session. Unauthenticated requests get `401`
or a redirect to the login page.

### CSRF

Reads are safe. **Writes require the CSRF token** in an `X-CSRFToken` header —
the session is a cookie, so exempting them would reopen CSRF:

```js
fetch('/api/v1/plants/<id>/care', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
  body: JSON.stringify({ action: 'water' }),
  credentials: 'same-origin'
})
```

### Errors

```json
{ "error": "No such plant in your garden.", "status": 404 }
```

A record that doesn't exist and one belonging to another user both return `404`,
so responses can't be used to discover which ids are real.

---

## Endpoints

### `GET /me`

```json
{
  "user": {
    "id": "6f2a...", "email": "you@example.com", "username": "gurpreet",
    "full_name": "Gurpreet Singh", "role": "user", "status": "active"
  },
  "summary": {
    "plantings": 12, "total_plants": 19, "species": 9, "gardens": 2,
    "needs_water": 3, "needs_fertilizer": 1,
    "streak": 6, "on_time_rate": 0.87, "actions_30d": 41
  }
}
```

`password_hash` is never serialised.

### `GET /gardens`

```json
{ "gardens": [ { "id": "...", "name": "Balcony", "location": "South-facing", "plant_count": 7 } ] }
```

### `GET /gardens/<garden_id>/plants`

Every planting in one garden, with resolved image URLs.

### `GET /plants/due`

The dashboard's grouping, as data.

```json
{
  "overdue":   [ { "id": "...", "name": "Monstera", "days_until_water": -2, "urgency": "overdue" } ],
  "due_today": [],
  "upcoming":  [],
  "counts": { "overdue": 1, "due_today": 0, "upcoming": 11 }
}
```

`days_until_*` is negative when overdue, `0` today, positive in future — counted
in **calendar days**, so a plant due at 23:00 tonight reads `0`, not `1`.

### `GET /plants/<planting_id>`

One planting plus its 25 most recent care-log entries.

### `POST /plants/<planting_id>/care`

```json
{ "action": "water", "note": "soil was bone dry" }
```

`action` is one of `water`, `fertilize`, `prune`, `repot`, `harvest`, `note`.
Watering and fertilizing roll the matching due date forward by the plant's
interval; repotting brings the next watering forward, since disturbed roots need
water sooner. Returns the updated planting.

### `GET /catalog?q=&limit=`

Search the shared species catalog. `limit` defaults to 50, capped at 200.

### `GET /week`

A seven-day outlook. Anything already overdue is folded into today's entry
rather than dropping off the start.

```json
{ "week": [ { "date": "2026-03-10", "is_today": true,
              "water": ["Monstera", "Basil"], "fertilize": [], "total": 2 } ] }
```

---

## Non-API endpoints worth knowing

| Route | Purpose |
|---|---|
| `GET /healthz` | Liveness: `200` when MongoDB is up, `503` otherwise. Also reports storage backend and whether AI is enabled. |
| `POST /chat/send` | The chatbot. `{"message": "..."}` in, `{"reply": "..."}` out. Rate limited, with a per-user daily cap. |
| `GET /chat/history` | The current transcript. |
| `GET /admin/reminders/preview` | Admin only. What the next reminder sweep would send — read-only, sends nothing. |

## Rate limits

| Endpoint | Limit |
|---|---|
| `POST /auth/login` | 20/hour, 100/day |
| `POST /auth/signup` | 10/hour, 40/day |
| `POST /auth/forgot-password` | 5/hour, 20/day |
| `POST /chat/send` | 30/min, 300/hour, plus 100 messages/day per user |
| everything else | 600/hour |

Exceeding one returns `429`. Limits are per client IP except the chat daily cap,
which is per account.
