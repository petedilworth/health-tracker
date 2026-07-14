# Oura Sleep Analytics

A small Python pipeline that pulls [Oura Ring](https://ouraring.com) sleep data,
computes trend + percentile analytics, renders a dashboard, and emails it — run
automatically once a day by GitHub Actions.

This is a clean-Python rebuild of an earlier Jupyter notebook project. No
notebooks required.

## What it does

Each day the job:

1. Pulls the last ~30 days of Oura data (Personal Access Token auth).
2. Upserts it into `data/sleep_data_processed.csv` (the full history lives here).
3. Computes, for **sleep score** and **bedtime**:
   - daily value, 7-day and 30-day trailing averages
   - percentile ranks vs. *all* history (today's daily value vs. every daily
     value; today's 7-day avg vs. every 7-day avg; likewise 30-day)
4. Renders `images/dashboard.png` (trends, histograms, KPI table).
5. Commits the updated CSV + PNG back to the repo, and emails the dashboard.

Bedtime is stored as a decimal hour with a +24 shift after midnight (1:00am =
25.0), so averaging evening and after-midnight bedtimes works correctly.

## Project layout

```
src/oura/          # the package
  config.py        # secret + path loading
  client.py        # Oura API v2 client (PAT auth, pagination)
  processing.py    # metrics: rolling averages, percentiles, bedtime decimal
  storage.py       # CSV history with idempotent upsert
  viz.py           # charts -> dashboard PNG
  report_email.py  # Gmail SMTP send, image embedded in body
  pipeline.py      # orchestrates a full daily run
scripts/backfill.py  # one-time: seed full history from the API
run.py               # daily entry point
tests/               # unit tests (no network needed)
.github/workflows/daily.yml
```

## Setup

### 1. Get an Oura Personal Access Token
Create one at <https://cloud.ouraring.com/personal-access-tokens>. It's a single
long-lived token — no OAuth flow to manage.

### 2. Add GitHub repository secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `OURA_PAT` | your Oura Personal Access Token |
| `MAIL_TO` | where the daily email should go |
| `GMAIL_USER` | the Gmail address that sends it |
| `GMAIL_APP_PASSWORD` | a Gmail [App Password](https://myaccount.google.com/apppasswords) (needs 2FA on) — **not** your login password |

(If you skip the three mail secrets, the job still runs and commits data — it
just skips the email.)

### 3. Seed your history (once, locally)
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env with your OURA_PAT
python scripts/backfill.py  # pulls 2019-01-01 -> today into data/sleep_data_processed.csv
git add data/ && git commit -m "seed history" && git push
```

### 4. Let it run
The workflow (`.github/workflows/daily.yml`) runs at **13:00 UTC daily** and can
also be triggered manually from the **Actions** tab (“Run workflow”).

## Running locally

```bash
python run.py               # full run: pull, update, render, email
python run.py --no-email    # skip the email
python run.py --lookback 60 # re-pull a longer recent window
```

Behind a corporate network that breaks TLS? Set `OURA_VERIFY_TLS=false` in your
`.env`. Leave it unset everywhere else (GitHub Actions has clean certs).

## Tests

```bash
pip install pytest && pytest -q
```

## Adding more metrics later
Add the metric to `METRICS` in `processing.py`, map its raw column in
`storage.RAW_COLUMNS`, and add a `METRIC_CONFIG` entry in `viz.py`. The rolling
averages, percentiles, histograms, and KPI row are all driven by those lists.
