# Gardening Companion: product plan

Status: plan. The engineering state it builds on is described in
`ARCHITECTURE.md`; the personalisation design is in `ML_DESIGN.md`.

Assumptions are marked **(assumption)** and are the first things to validate.
Market and competitor claims are deliberately qualitative: verify current
features and pricing before quoting them anywhere.

## 1. Positioning

> Plant care that adapts to *your* home, not to a generic schedule.

Most plant apps are a species encyclopedia plus a fixed reminder. This one
differentiates on three things that compound:

1. **A schedule that learns** from what the user actually does and, outdoors,
   from the weather (`ML_DESIGN.md`).
2. **AI grounded in the user's own garden and a large curated catalog**: chat
   that knows what is overdue, search by need ("pet-safe, low light"), and photo
   diagnosis that proposes a schedule change the user can accept.
3. **A catalog that scales honestly**: a reproducible pipeline (Wikipedia,
   Wikidata, GBIF, AI enrichment) with provenance and measured accuracy, rather
   than a hand-typed list.

## 2. Users and jobs to be done

| Persona | Situation | Job | Success looks like |
|---|---|---|---|
| **The serial plant killer** | 3–10 houseplants, keeps losing them | "Tell me exactly what to do today so I stop killing them" | plants alive at 60 days |
| **The collector** | 30+ plants, several rooms | "Show me what needs attention, ignore the rest" | one glance, one digest email |
| **The balcony / veg grower** | outdoor containers, weather-dependent | "Don't make me water before rain" | fewer wasted or missed waterings |

**(assumption)** The first persona is the entry point: highest pain, lowest
setup tolerance. Onboarding must reach a first useful reminder in under two
minutes.

## 3. Competitive landscape (qualitative)

| Category | Typical strength | Typical gap this product targets |
|---|---|---|
| Plant ID / diagnosis apps | strong image recognition, big databases | schedules are static; diagnosis rarely changes the plan |
| Reminder-centred care apps | polished reminders, habit loops | generic intervals; weak on outdoor and weather |
| Garden-planning tools | layout and seasonal planting | not day-to-day care |
| Generic task apps | flexible | no plant knowledge |

Honest read: recognition and catalogue size are commodities and well funded.
Competing on them head-on is a mistake. The defensible ground is **the
feedback loop**: logs, outcomes and weather feeding the schedule, with
reasoning shown to the user. That ground only exists if the instrumentation in
`ML_DESIGN.md` section 7 ships early.

## 4. MVP scope

**In (largely built):** accounts and gardens, catalog with search, schedules by
life stage, dashboard, daily digest email, chat, admin curation, REST API.

**In (next):** semantic search, grounded chat, photo diagnosis, eval harness
(roadmap phase 1).

**Deliberately out of the MVP:** native apps, social features, a marketplace,
plant-ID-from-photo as a headline feature, smart-sensor integrations.

## 5. Gaps between "project" and "product"

| Area | Today | Needed |
|---|---|---|
| Delivery channel | web + email | installable PWA, web push; email stays the fallback |
| Onboarding | sign up, then empty dashboard | guided first plant, sample garden, time-to-first-reminder tracked |
| Notifications | hourly digest | per-user timing (Task B), quiet hours, per-garden mute |
| Identity and privacy | email, Google sign-in | account export and deletion, consent for location, privacy policy, cookie notice |
| Billing | none | Stripe subscriptions, entitlements enforced server-side |
| Observability | request IDs, logs | metrics endpoint, traces, error tracking, AI cost dashboard |
| Reliability | single instance assumed | health-checked deploy, backups with a tested restore, reminder sweep fan-out |
| Abuse and cost control | rate limits, daily chat cap | per-plan AI quotas, upload scanning, moderation of user-submitted catalog requests |
| Quality gates | unit tests, CI | end-to-end tests of the reminder path, load test, migration tooling |
| Content rights | Wikimedia image URLs | attribution surfaced in UI, license per image stored |

## 6. Data and content obligations

- **Image and text licenses.** Wikipedia text is CC BY-SA and most Wikimedia
  images need attribution; the pipeline stores source URLs, and the UI must
  show attribution before any public launch.
- **Safety content.** Edibility and pet-toxicity are safety claims. The
  `edible` flag is currently an AI/heuristic guess. Before presenting it as
  fact, source toxicity from an authoritative list, label AI-derived values as
  such, and add a disclaimer. This is the highest-liability item in the product.
- **Provenance.** Every care value carries `care_source` and, for AI values,
  model and prompt version. Keep it visible to admins and in exports.

## 7. Monetisation (hypothesis to test, not a commitment)

Freemium **(assumption)**:

| | Free | Plus |
|---|---|---|
| Plants | up to ~10 | unlimited |
| Reminders and digest | yes | yes, with smart timing |
| Weather-adaptive schedule | limited | yes |
| Photo diagnosis | few per month | higher quota |
| Chat | capped | higher cap |
| Household sharing | no | yes |

**Unit economics: measure, don't guess.** The only variable cost that scales
with usage is AI. The `ai_calls` collection (tokens, latency, cost by feature)
exists to answer:

```
monthly AI cost per active user =
    chats/user * tokens/chat * price_per_token
  + diagnoses/user * tokens/diagnosis * price_per_token
  + embeddings (negligible after the one-off index)
```

Set free-tier quotas so the free user's AI cost stays a small, known fraction of
what a paying user covers, using measured usage from the beta.

## 8. Metrics

North star: **weekly active gardeners**, meaning users who log at least one care
action in the week. It rewards value delivered, not logins.

| Stage | Metric |
|---|---|
| Activation | signed up -> first plant added -> first reminder acted on, within 7 days |
| Engagement | actions per active user per week; share of reminders acted on within 24h |
| Retention | week-4 and week-8 retention; **plants still tracked at 60 days** |
| Outcome | plants reported thriving at day 30 and 60 (needs the health prompt) |
| Quality | late-action rate, snoozes per reminder |
| AI | cost per active user, diagnosis acceptance rate, chat thumbs-up rate |
| Reliability | reminder delivery success, p95 latency, error rate |

Every metric needs an event. The event schema is part of phase 3.

## 9. Roadmap

Effort is for one developer working part-time, as a planning aid rather than a
promise.

| Phase | Theme | Scope | Exit criteria | Rough effort |
|---|---|---|---|---|
| **0** | Foundation (done) | app, tests, CI, pipeline, enrichment, semantic search | 243 tests green; 1,000+ species catalog with a data-quality report | done |
| **1** | AI depth | chat grounded on retrieved species; photo diagnosis; eval harness (retrieval recall/MRR, diagnosis set, enrichment agreement); `ai_calls` observability | numbers published in README; evals run in CI | 3–4 weeks |
| **2** | Personalisation | instrumentation (snooze events, reminder events, health prompt, pot/location); feature module; simulator; empirical-Bayes estimator; weather adjustment | beats static baseline in simulator backtest; estimator never worse than prior with no history | 3–4 weeks |
| **3** | Product hardening | PWA + web push, onboarding, privacy (export, delete, consent), attribution, safety labelling, analytics events, error tracking, backups, deploy pipeline | a stranger can sign up, add a plant and get a push in under 2 minutes | 4–5 weeks |
| **4** | Closed beta | 20–50 real users, weekly review of the section 8 metrics, fix the top retention leak | week-4 retention and plants-alive-at-60-days measured; first real evaluation of the estimator | 6–8 weeks elapsed |
| **5** | Monetise | Stripe, entitlements, quotas from measured AI cost | paying users cover measured per-user variable cost | 3–4 weeks |

Order matters: phase 2 instrumentation should be pulled forward into phase 1,
because data can only be collected from the day the events exist.

## 10. Engineering target state

```
 PWA (web push)          Flask API            Workers (Celery)
 ───────────────        ───────────          ─────────────────
 onboarding, today       blueprints ▸ services    reminder fan-out (per user)
 view, search, chat      ▸ repositories           weather refresh (per bucket)
        │                      │                  reindex, health prompts
        ▼                      ▼                          │
   service worker        MongoDB ◂──────────────────────┘
                         Redis: cache, rate limits, queues
                               │
              ┌────────────────┼──────────────────┐
              ▼                ▼                  ▼
        Gemini (chat,     Open-Meteo        S3 (images)
        vision, embed)    (weather)
```

Principles carried over from the current code: services never touch `request`;
every external dependency degrades to a working fallback; every AI output is
schema-validated and tagged with provenance.

## 11. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Safety claims (edible, toxic) are wrong | user or pet harm, liability | authoritative sources, AI labelling, disclaimers, human review of flagged species |
| AI cost grows faster than revenue | negative margin | per-plan quotas, caching, measured unit economics, provider abstraction |
| Free-tier AI quotas (500 requests/day observed) | can't serve beta traffic | paid tier before beta; batch and cache enrichment; degrade gracefully |
| Crowded market | no traction | focus on the feedback-loop differentiator; talk to users in phase 4 |
| No real users yet | every ML claim unproven | simulator for correctness only; beta before any accuracy claim |
| Scope creep | nothing ships | the phase exit criteria above; out-of-scope list in section 4 |

## 12. Portfolio presentation checklist

The same work doubles as evidence of engineering maturity. For the repository:

- [ ] README hero: one screenshot or GIF, three-line pitch, architecture diagram
- [ ] Results table in the README: retrieval recall@k / MRR, enrichment agreement
      vs curated data, pipeline funnel, p95 search latency
- [ ] `docs/decisions/`: short ADRs (exact vector search vs a vector DB; separate
      pipeline; empirical Bayes before ML; fill-only imports)
- [ ] CI badges; coverage published
- [ ] Issues and milestones mirroring section 9, closed by PRs with clear commit
      messages
- [ ] A short "limitations and what I would do next" section: the honest ones
      here (cold start, safety data, free-tier quotas) read as maturity
