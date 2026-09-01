"""Turn stored history into the full computed metric set.

Order matters here:
  exclusions -> quality gates -> daily reindex -> seasonal z-scores ->
  need/debt -> score components -> score -> readiness -> SRI -> flags -> rollups

Everything is recomputed from full history on every run, so the numbers always
reflect the current exclusions list and the latest data.
"""

from __future__ import annotations

import logging

import pandas as pd

from . import config, flags, metrics, quality, regularity, score, seasonal, store
from .schema import ALL_METRICS, SCORE_COMPONENTS

log = logging.getLogger("sleep.compute")

# Metrics that get 7d/30d averages and percentile columns on the site.
ROLLUP_COLUMNS = [m.key for m in ALL_METRICS] + [
    "sleep_score", "sleep_debt_h", "sleep_need_h", "sleep_recommended_h",
    "sleep_performance_pct", "readiness", "sri",
]

# Steps had an unusable early era in this dataset; detected, not hardcoded.
RELIABILITY_CHECKED = ["steps"]


def compute(history: pd.DataFrame | None = None,
            exclusions: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Return (daily metrics frame indexed by date, summary dict)."""
    if history is None:
        history = store.load_history(config.HISTORY_PATH)
    if exclusions is None:
        exclusions = store.load_exclusions(config.EXCLUSIONS_PATH)

    if history.empty:
        return pd.DataFrame(), {"nights": 0}

    n_raw = len(history)
    history = store.apply_exclusions(history, exclusions)
    history, dropped = quality.clamp_implausible(history)
    history, reliable_starts = quality.apply_reliable_starts(history, RELIABILITY_CHECKED)

    daily = metrics.to_daily(history)

    # A clock change distorts wall-clock timing comparisons for that night only.
    span = daily["waketime"] - daily["bedtime"]
    daily["dst_night"] = (span - daily["time_in_bed_h"]).abs().sub(1.0).abs() <= 0.1

    daily = seasonal.add_z_scores(daily)
    daily = daily.join(score.sleep_debt_and_need(daily))

    components = score.component_scores(daily)
    daily = daily.join(components)
    daily["sleep_score"] = score.sleep_score(components)
    daily["readiness"] = score.readiness(daily, daily["sleep_score"])
    daily["sri"] = regularity.sleep_regularity_index(daily)
    daily = daily.join(flags.health_flags(daily))

    daily = metrics.add_rollups(daily, ROLLUP_COLUMNS)

    summary = _summarise(daily, n_raw, len(history), dropped, reliable_starts)
    return daily, summary


def _summarise(daily: pd.DataFrame, n_raw: int, n_kept: int,
               dropped: dict, reliable_starts: dict) -> dict:
    scored = daily["sleep_score"].dropna()
    latest = daily[daily["sleep_score"].notna()].tail(1)

    summary = {
        "nights_recorded": n_raw,
        "nights_after_exclusions": n_kept,
        "nights_scored": int(len(scored)),
        "date_range": (
            f"{daily.index.min().date()} → {daily.index.max().date()}"
            if len(daily) else "—"
        ),
        "values_clamped": dropped,
        "reliable_starts": {k: str(v) for k, v in reliable_starts.items()},
        "flag_nights": int(daily["flag_raised"].sum()) if "flag_raised" in daily else 0,
    }
    if not latest.empty:
        row = latest.iloc[0]
        summary["latest"] = {
            "day": str(latest.index[0].date()),
            "sleep_score": round(float(row["sleep_score"]), 1),
            "sleep_performance_pct": _maybe_round(row.get("sleep_performance_pct")),
            "sleep_debt_h": _maybe_round(row.get("sleep_debt_h")),
            "sri": _maybe_round(row.get("sri")),
            "readiness": _maybe_round(row.get("readiness")),
            "flag": bool(row.get("flag_raised", False)),
        }
    return summary


def _maybe_round(v, nd: int = 1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def save(daily: pd.DataFrame, path=None) -> None:
    """Cache the computed frame for the site and email stages to consume."""
    path = path or (config.DATA_DIR / "computed.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(path, index_label="day")
    log.info("Wrote computed metrics -> %s (%d rows)", path, len(daily))
