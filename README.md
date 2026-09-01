# Sleep Analytics

Custom analytics on top of [Oura Ring](https://ouraring.com) data — a personal
sleep score, sleep debt, sleep regularity and readiness, published as a website
and a daily email, all automated through GitHub Actions.

> **Build status: Stages 1–2 complete (data layer + metrics engine).**
> Stage 3 website and Stage 4 email are in progress. The daily job pulls,
> stores and computes all metrics; it does not yet publish or email.

## Why this exists

Oura's own sleep score compresses almost every night into a narrow 70–85 band,
which makes it useless for spotting real change. This rebuild scores each
component **relative to your own history**, weighted by how reliably the ring can
actually measure it.

## Measurement confidence

Independent validation against polysomnography (not Oura's marketing) puts the
signals in three tiers, and that tiering drives both the score weights and how
the site renders each metric:

| Tier | Metrics | Why |
|---|---|---|
| **High** | HR, HRV, respiratory rate, temperature, bedtime, time in bed, total sleep, steps | Direct PPG/thermistor measurement; 94%+ sleep/wake sensitivity |
| **Moderate** | Efficiency, restfulness | Wake specificity is only 29–52%, so efficiency runs optimistic (+1.75–7.9%) and compresses near the top |
| **Low** | REM, deep | Four-stage agreement with PSG ~76–79%; directional, not exact |

Sources: [JMIR 11-tracker validation](https://mhealth.jmir.org/2023/1/e50983) ·
[SLEEP Advances](https://academic.oup.com/sleepadvances/article/6/2/zpaf021/8090472) ·
[three-wearable accuracy study](https://pmc.ncbi.nlm.nih.gov/articles/PMC11511193/) ·
[Oura Gen3 OSSA 2.0](https://www.sciencedirect.com/science/article/pii/S1389945724000200)

Because a roughly constant bias cancels out when you compare yourself to
yourself, every score component is graded against your own baseline rather than
an absolute target.

## Sleep score weights

| Component | Weight | Confidence |
|---|---|---|
| Bedtime, sleep duration (vs need), timing, HR low, HRV, respiratory rate | 1.0 each | High |
| Efficiency, restfulness | 0.5 each | Moderate |
| REM, deep | 0.25 each | Low |

Total weight 7.5, normalised to 0–100. Body temperature is tracked and flagged
but deliberately kept **out** of the score — it signals illness, not sleep
quality.

## Running it — everything happens in GitHub Actions

No local Python needed. All three workflows run from the **Actions** tab (and
from the GitHub mobile app).

| Workflow | What it does |
|---|---|
| **Daily Sleep Analytics** | Runs at 13:00 UTC. Pulls the last 30 days and updates `data/history.csv`. |
| **Backfill history** | One-off. Pulls your entire history (default from 2019-01-01). Run this once. |
| **Anomaly report** | Builds a review queue of suspicious nights into [`docs/review/anomalies.md`](docs/review/anomalies.md), with surrounding nights for context. |
| **Exclude a day** | Removes bad nights from every metric. Accepts several comma-separated dates. Set `action: include` to restore them. |
| **Recalibration review** | Opens an issue each 1 March with the sleep-need baseline comparison, so that judgement call gets re-argued against current data. |

## Data quality

Three gates run before any metric is computed:

- **Clamping** — individual readings outside physiological range are nulled
  (e.g. a +5.28°C temperature deviation). The value is dropped, not the night.
- **Reliable-start detection** — a metric with a bad early era is truncated from
  a date derived from the data, not hardcoded. Oura logged almost no steps
  before 2021, so steps is automatically usable from 2021-02-06 onward; sleep
  metrics keep full history.
- **Anomaly review** — suspicious nights are queued for a human decision, never
  auto-deleted. Daylight-saving clock changes are identified and kept, since
  only the bedtime→waketime arithmetic is affected, not the recorded duration.

Exclusions live in `data/exclusions.csv` and are always reversible.

### First-time setup

Add these under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `OURA_PAT` | Personal Access Token from [cloud.ouraring.com](https://cloud.ouraring.com/personal-access-tokens) |
| `MAIL_TO` | Where the daily email goes *(used from Stage 4)* |
| `RESEND_API_KEY` | API key from [resend.com](https://resend.com/api-keys), sending access *(Stage 4)* |
| `MAIL_FROM` | *Optional.* Defaults to Resend's sandbox sender. |

Then run **Backfill history** once to seed `data/history.csv`.

## Data model

`data/history.csv` holds one row per day of **raw measurements only**. Rolling
averages, percentiles and the score are recomputed over full history on every
run, so they always reflect the current exclusions.

`data/exclusions.csv` lists removed days with a reason and timestamp.

Two conventions worth knowing:

- **Bedtime** is a decimal hour with a +24 shift after midnight (1:00am = 25.0),
  so averaging an 11pm and a 1am bedtime doesn't produce noon. Local wall-clock
  time is used, so travel across timezones doesn't distort it.
- **Naps** are summed into `nap_sleep_h` and never scored — they pay down sleep
  debt, but efficiency, timing and stage percentages are only meaningful for a
  consolidated night.

## The metrics

| Metric | What it is |
|---|---|
| **Sleep score** | Weighted composite, every component graded against your own history |
| **Sleep need** | Stable baseline: rolling 180-day 75th percentile of your sleep (~7.2h) |
| **Recommended tonight** | Need plus debt repayment and an allowance for an active day |
| **Sleep performance %** | Actual sleep ÷ need |
| **Sleep debt** | Exponentially decaying cumulative shortfall (τ ≈ 7 days) — naps repay it |
| **SRI** | Sleep Regularity Index, 0–100, over a trailing 30 days |
| **Readiness** | HRV, resting HR, respiratory rate and last night's score |
| **Health flag** | Fires when temperature or respiratory rate exceeds 2 SD from its seasonal baseline |

Measured against 2,722 nights, the custom score has **1.5× the standard
deviation and 1.66× the interquartile range** of Oura's own score, while
correlating 0.65 with it — the same underlying nights, spread across a range
that actually discriminates.

## Project layout

```
src/sleep/
  config.py      # secrets + paths
  schema.py      # canonical columns + confidence tiers
  client.py      # Oura API v2 (sleep, daily_sleep, daily_activity, daily_readiness)
  transform.py   # raw records -> one row per day
  store.py       # CSV history, idempotent upsert, exclusions
  quality.py     # clamping, reliable-start detection, anomaly detection
  ingest.py      # pull -> transform -> upsert orchestration
  seasonal.py    # month-effect de-trending, rolling baselines
  metrics.py     # daily reindexing, rolling averages, percentiles, aggregation
  regularity.py  # Sleep Regularity Index
  score.py       # sleep score, need, debt, performance, readiness
  flags.py       # combined health flag
  compute.py     # full metric pipeline -> data/computed.csv
scripts/backfill.py, exclude_day.py, anomaly_report.py, validate_score.py,
         need_calibration.py
run.py           # daily entry point
tests/           # 59 tests, no network required
```

`data/computed.csv` is derived and git-ignored — it is regenerated from
`history.csv` and `exclusions.csv` on every run.

## Local development (optional)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # add your OURA_PAT
pytest -q
```
