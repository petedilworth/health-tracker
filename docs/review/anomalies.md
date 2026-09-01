# Anomaly review

**0 nights need a decision** out of 2761 total (0.0%).

For each night below: does it look like real (if unusual) sleep, or a data error? When in doubt, exclude — excluding is reversible, and the metrics recompute from full history every run.

## How to action this

1. Copy the date list at the bottom.
2. Delete any dates you want to **keep**.
3. Go to **Actions → Exclude a day → Run workflow**, paste the remaining dates into `date`, set a reason, and run.

## Values auto-nulled as physiologically impossible

These individual readings were dropped; the rest of each night is intact and needs no decision from you.

- `bedtime`: 3 value(s)
- `temp_deviation`: 2 value(s)
- `temp_trend_deviation`: 2 value(s)
- `total_sleep_h`: 1 value(s)

## Auto-detected reliable start dates

These metrics had an unreliable early era, detected from the data rather than hardcoded. Earlier values are excluded from that metric only.

- `steps`: usable from **2021-02-06**

## Explained automatically — no action needed (6)

Daylight-saving clock changes. The recorded duration is correct; only the bedtime→waketime subtraction is distorted, so these nights are kept and only their `timing` component is skipped.

- 2023-10-29
- 2024-03-31
- 2024-10-27
- 2025-03-30
- 2025-11-02
- 2026-03-08

## Needs your decision (0)

---

## Date list to copy

Delete any you want to keep, then paste the rest into the **Exclude a day** workflow:

```

```