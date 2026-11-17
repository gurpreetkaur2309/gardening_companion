# Personalised care: feature engineering & model design

Status: design. Nothing in this document is built yet; each section says what
data exists today and what must be instrumented first.

## 1. The problem

A species schedule ("water basil every 3 days") is a prior, not an answer. The
real interval depends on the pot, the light, the room, the season, the weather
and the person. Today the app applies the same number to everyone and learns
nothing from what users actually do.

**Goal:** turn the stream of care actions into a per-planting interval estimate
that is better than the static species number, and explain it.

Three prediction tasks, in the order they are worth building:

| # | Task | Output | Why |
|---|---|---|---|
| A | Next-watering interval for one planting | days (with uncertainty) | the core product promise |
| B | Reminder timing | hour-of-day / day-of-week to send | reminders nobody acts on are noise |
| C | Plant-at-risk score | probability a planting is neglected / fails | triage for collectors, retention signal |

## 2. What the data can support today

From `care_logs`, `garden_plants`, `gardens`, `plants`:

| Signal | Exists | Notes |
|---|---|---|
| Timestamped care actions | yes | `care_logs.created_at`, `action` |
| Lateness of an action | yes | `was_late`, judged against the due date being settled |
| Planting age and stage | yes | `planted_at`, `age_stage` |
| Species prior | yes | `plants.care`, with `care_source` provenance (curated / ai / default) |
| Indoor vs outdoor, light | **no** | `gardens.location` is free text |
| Pot size, soil | **no** | |
| Geography / climate | **no** | |
| Snoozes | **no** | only the due date moves; the *event* is lost |
| Reminder delivered / opened | **no** | `reminder_log` records sends, not engagement |
| Outcome (thriving / died) | **no** | no label for Task C at all |

**Consequence:** Task A can start with data the app already collects. Task C
cannot start until an outcome label exists. Instrumentation (section 7) is the
first deliverable, ahead of any model.

## 3. Cold start

There are no users, so there are no logs. Two honest consequences:

1. **No model claim can be made yet.** A model trained on synthetic data
   demonstrates the pipeline, not the product. The README must say so.
2. **Build for the cold start anyway.** The estimator must return the species
   prior when a planting has no history, and move away from it only as evidence
   accumulates. That is a design requirement, not an afterthought.

A **log simulator** (`tools/simulate_logs.py`) generates synthetic users with a
hidden "true interval" per planting, behavioural noise (lateness, skipped
waterings, burst logging) and seasonal drift. It exists to test the feature
code and evaluation harness against a known ground truth: if the estimator
cannot recover a planted signal, it will not find a real one.

## 4. Feature engineering

Features are computed by one pure module (`app/ml/features.py`): data in,
features out, no I/O. The same code runs in the offline backtest and in
production, which is what prevents train/serve skew.

### 4.1 Planting features

| Feature | Definition |
|---|---|
| `prior_interval` | species watering days, scaled by life stage (existing multiplier) |
| `prior_source` | curated / ai / default; the prior's reliability |
| `age_days`, `stage` | days since `planted_at`; stage enum |
| `is_succulent`, `sunlight`, `family` | from the catalog |
| `location_class` | indoor / outdoor / unknown, derived from garden location text, later asked directly |

### 4.2 Behavioural features (from `care_logs`)

Computed over the planting's own history, with a strict `as_of` timestamp.

| Feature | Definition |
|---|---|
| `n_waterings` | count in the lookback window (the evidence weight) |
| `realised_interval_median`, `_mad` | median and median-absolute-deviation of gaps between waterings |
| `realised_over_prior` | ratio of realised to prior interval |
| `late_rate` | share of waterings with `was_late` |
| `mean_lateness_days` | average days past due when late |
| `burst_rate` | share of logs within 12h of the previous one (bulk "I watered everything" logging, which is not a real interval) |
| `days_since_last_water` | recency |
| `weekday_profile` | normalised distribution of actions over weekdays |

Gaps shorter than a floor (default 12 hours) are dropped as duplicate logging,
not treated as a 0-day interval. Repot events reset the lookback window, because
a repotted plant is a different plant.

### 4.3 Context features

| Feature | Source | Applies to |
|---|---|---|
| `season`, `hemisphere` | date + user latitude | all |
| `et0_since_last_water` (mm) | Open-Meteo reference evapotranspiration | outdoor |
| `precip_since_last_water` (mm) | Open-Meteo | outdoor |
| `mean_temp_7d`, `mean_humidity_7d` | Open-Meteo | outdoor, weakly indoor |

Weather is the one feature family that adds information **with no user history
at all**, so it is also the first one worth shipping (section 6, step 1).

### 4.4 Leakage rules

- Every feature takes an explicit `as_of`; nothing may read a log at or after it.
- Backtests split **by user, then by time**. Random row splits leak a user's
  habits into both sides and overstate accuracy.
- The label (the *next* realised interval) is never visible to its own features.

## 5. Model: start simple, justify each step up

The right first model here is not gradient boosting.

**Step 1: empirical-Bayes interval estimator (no ML framework).**

Treat each realised gap as a noisy observation of the planting's true interval
and shrink toward the species prior:

```
estimate = (n * median_gap + k * prior) / (n + k)
```

`k` is the prior's strength, set from `prior_source` (curated high, AI medium,
default low) and tuned in the backtest. With no history `n = 0`, so the estimate
is the prior. With a lot of history it is the user's own pattern. It is
explainable in one sentence ("based on your last 6 waterings"), cannot behave
wildly on tiny data, and is a few lines of code.

**Step 2: weather adjustment (outdoor).** Scale the estimate by the ratio of
recent to seasonal-normal ET0, bounded (default 0.6–1.5) so a hot week cannot
produce nonsense.

**Step 3: learned residual model, only if steps 1–2 leave measurable error.**
Gradient-boosted trees on the section 4 features predicting the residual of
step 1. Introduced only if it beats step 2 in the backtest by a margin that
survives a user-level holdout. If it does not, it does not ship, and that result
goes in the README.

**Task B (timing):** per-user circular histogram of action times; send inside
the user's modal window. No model.

**Task C (risk):** logistic regression on `late_rate`, `realised_over_prior`,
recency and age, once the outcome label exists.

## 6. Evaluation

### Offline (per task A)

Replay each user's history chronologically: at each watering, predict the next
interval from data before it, compare with what happened.

| Metric | Why |
|---|---|
| MAE (days) vs the static-schedule baseline | the headline number |
| Within-1-day rate | what a user would notice |
| Calibration of the interval | do 80% intervals contain 80% of outcomes |
| MAE by history length (0, 1–2, 3–5, 6+) | proves the cold-start behaviour |
| MAE by `prior_source` | does the AI prior hurt or help |

Baselines, in order: static species schedule; the user's last interval;
the user's median interval. The estimator must beat all three to ship.

### Online (once there are users)

| Metric | Direction |
|---|---|
| Reminder to action rate within 24h | up |
| Share of waterings logged `was_late` | down |
| Snoozes per reminder | down |
| 30/60-day plant retention (plant still tracked) | up |

Roll out per user behind a flag, 50/50 against the static schedule. Success is
a statistically meaningful move in late rate or reminder action rate, not a
better offline MAE.

### Caveat to state plainly

Realised intervals measure **what users did**, not **what the plant needed**. A
neglectful user's median gap is too long; learning it would teach the app to
remind them less. The estimator therefore only moves the schedule within
bounds around the prior (default 0.5x–2x) and the UI shows the reasoning. The
outcome label in section 7 is what eventually lets the model learn the
difference.

## 7. Instrumentation (build first)

| Event / field | Purpose |
|---|---|
| `snooze` written to `care_logs` (with days) | behavioural signal currently discarded |
| `reminder_events`: sent, opened, acted | engagement label for Task B, product metric |
| `plantings.health` (thriving / struggling / died / given_away), prompted ~30 days after adding | the outcome label; the most valuable single field |
| `plantings.pot_size`, `location_class` (asked at add time, optional) | missing static features |
| user `timezone` and coarse location (city-level, opt-in) | weather and timing |
| `ai_calls` collection (model, tokens, latency, cost, feature) | AI observability and unit economics |
| `schedule_decisions`: prior, estimate, features, version | audit trail, and the join key for online evaluation |

Privacy: coarse location only, opt-in, deletable with the account; no raw
coordinates stored.

## 8. Serving

- `app/ml/features.py` and `app/ml/estimator.py`: pure and unit-tested.
- Computed at log time (`record_care`), not in a batch job: the estimate is a
  handful of arithmetic operations over one planting's recent logs.
- Weather fetched by a daily Celery task per location bucket (not per user),
  cached in Redis.
- Every estimate is stored with its inputs and a `model_version` in
  `schedule_decisions`, so a bad release can be identified and rolled back.
- Fail-safe: any error in the estimator falls back to the static schedule.

## 9. Risks

| Risk | Mitigation |
|---|---|
| No users, so no real validation | simulator for correctness; state the limitation; beta with instrumentation first |
| Learning user neglect | bounded adjustment around the prior; outcome label |
| Weather API outage or rate limit | location-bucketed cache; estimator works without it |
| Small-data instability | shrinkage toward the prior; minimum evidence before any change |
| Over-engineering | each step must beat the previous one in the backtest to ship |
