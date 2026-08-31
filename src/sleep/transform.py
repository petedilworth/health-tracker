"""Turn raw Oura API records into canonical one-row-per-day records.

Design notes
------------
* Bedtime is stored as a *decimal hour with a +24 shift after midnight*, so
  1:00am becomes 25.0 rather than 1.0. Without this, averaging an 11pm bedtime
  (23.0) with a 1am bedtime (1.0) would wrongly produce noon (12.0).
* Only `long_sleep` sessions are scored. Naps are summed separately into
  `nap_sleep_h`: they pay down sleep debt but never enter the sleep score,
  because efficiency, timing and stage percentages are only meaningful for a
  consolidated night.
* Durations arrive from Oura in seconds and are converted to hours here, so
  every downstream consumer works in one unit.
"""

from __future__ import annotations

import pandas as pd

from .schema import RAW_COLUMNS

SECONDS_PER_HOUR = 3600.0
LONG_SLEEP = "long_sleep"


def _to_decimal_hour(series: pd.Series) -> pd.Series:
    """ISO datetime string -> decimal hour, +24 shift for after-midnight times.

    Oura returns local time with a UTC offset, e.g. '2024-01-01T23:30:00-05:00'.
    "When did I go to bed" is a local-time question, so we take the wall-clock
    portion (the first 19 characters) and ignore the offset entirely. Slicing the
    string sidesteps pandas' mixed-offset parsing, which is what travel across
    timezones produces in this dataset.
    """
    local = pd.to_datetime(
        series.astype("string").str.slice(0, 19), errors="coerce"
    )
    hours = local.dt.hour + local.dt.minute / 60 + local.dt.second / 3600
    return hours.where(hours >= 12, hours + 24)


def _hours(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce") / SECONDS_PER_HOUR


def sleep_sessions_to_frame(records: list[dict]) -> pd.DataFrame:
    """Sleep sessions -> one row per day of long-sleep metrics, plus nap totals."""
    empty = pd.DataFrame(columns=[
        "day", "bedtime", "waketime", "total_sleep_h", "time_in_bed_h",
        "efficiency", "latency_min", "restless_periods", "deep_h", "rem_h",
        "light_h", "hr_low", "hr_avg", "hrv", "breaths_per_min", "nap_sleep_h",
    ])
    if not records:
        return empty

    df = pd.DataFrame(records)
    if "day" not in df.columns:
        return empty
    df["day"] = pd.to_datetime(df["day"]).dt.date

    sleep_type = df["type"] if "type" in df.columns else pd.Series(LONG_SLEEP, index=df.index)

    # --- naps: summed per day, used only for debt repayment ---
    naps = df[sleep_type != LONG_SLEEP]
    nap_totals = (
        naps.assign(nap_sleep_h=_hours(naps.get("total_sleep_duration")))
        .groupby("day", as_index=False)["nap_sleep_h"].sum()
        if not naps.empty else pd.DataFrame(columns=["day", "nap_sleep_h"])
    )

    # --- the main night ---
    nights = df[sleep_type == LONG_SLEEP].copy()
    if nights.empty:
        return empty.merge(nap_totals, on="day", how="outer") if not nap_totals.empty else empty

    out = pd.DataFrame({"day": nights["day"]})
    out["bedtime"] = _to_decimal_hour(nights["bedtime_start"])
    out["waketime"] = _to_decimal_hour(nights["bedtime_end"])
    out["total_sleep_h"] = _hours(nights.get("total_sleep_duration"))
    out["time_in_bed_h"] = _hours(nights.get("time_in_bed"))
    out["deep_h"] = _hours(nights.get("deep_sleep_duration"))
    out["rem_h"] = _hours(nights.get("rem_sleep_duration"))
    out["light_h"] = _hours(nights.get("light_sleep_duration"))
    out["latency_min"] = pd.to_numeric(nights.get("latency"), errors="coerce") / 60.0
    for src, dest in [
        ("efficiency", "efficiency"), ("restless_periods", "restless_periods"),
        ("lowest_heart_rate", "hr_low"), ("average_heart_rate", "hr_avg"),
        ("average_hrv", "hrv"), ("average_breath", "breaths_per_min"),
    ]:
        out[dest] = pd.to_numeric(nights.get(src), errors="coerce")

    # If a day somehow holds two long sleeps, keep the longer one.
    out = out.sort_values("total_sleep_h", ascending=False).drop_duplicates("day", keep="first")

    if not nap_totals.empty:
        out = out.merge(nap_totals, on="day", how="outer")
    else:
        out["nap_sleep_h"] = 0.0
    out["nap_sleep_h"] = out["nap_sleep_h"].fillna(0.0)
    return out


def _daily_frame(records: list[dict], mapping: dict[str, str],
                 contributors: dict[str, str] | None = None) -> pd.DataFrame:
    """Generic 'daily_*' endpoint -> DataFrame of the requested fields."""
    cols = ["day"] + list(mapping.values()) + list((contributors or {}).values())
    if not records:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(records)
    if "day" not in df.columns:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({"day": pd.to_datetime(df["day"]).dt.date})
    for src, dest in mapping.items():
        out[dest] = pd.to_numeric(df.get(src), errors="coerce")
    for src, dest in (contributors or {}).items():
        if "contributors" in df.columns:
            out[dest] = pd.to_numeric(
                df["contributors"].apply(
                    lambda c: c.get(src) if isinstance(c, dict) else None
                ),
                errors="coerce",
            )
        else:
            out[dest] = pd.NA
    return out.drop_duplicates("day", keep="last")


def daily_sleep_to_frame(records: list[dict]) -> pd.DataFrame:
    """Oura's own sleep score (stored for comparison) and the restfulness contributor."""
    return _daily_frame(
        records,
        mapping={"score": "oura_sleep_score"},
        contributors={"restfulness": "restfulness"},
    )


def daily_activity_to_frame(records: list[dict]) -> pd.DataFrame:
    return _daily_frame(records, mapping={"steps": "steps"})


def daily_readiness_to_frame(records: list[dict]) -> pd.DataFrame:
    return _daily_frame(
        records,
        mapping={
            "score": "oura_readiness_score",
            "temperature_deviation": "temp_deviation",
            "temperature_trend_deviation": "temp_trend_deviation",
        },
    )


def build_rows(payloads: dict[str, list[dict]]) -> pd.DataFrame:
    """Combine all four endpoint payloads into canonical one-row-per-day records."""
    frames = [
        sleep_sessions_to_frame(payloads.get("sleep", [])),
        daily_sleep_to_frame(payloads.get("daily_sleep", [])),
        daily_activity_to_frame(payloads.get("daily_activity", [])),
        daily_readiness_to_frame(payloads.get("daily_readiness", [])),
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=RAW_COLUMNS)

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="day", how="outer")

    for col in RAW_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA
    return merged[RAW_COLUMNS].sort_values("day").reset_index(drop=True)
