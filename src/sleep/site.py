"""Static site generator: computed frame -> docs/ for GitHub Pages.

Python does all the shaping (formatting rules, percentile direction, aggregates,
top/bottom lists, distributions, component breakdowns) so the browser-side JS
stays a thin chart driver. Pages are plain HTML from f-string templates; data
ships as JSON per metric.

Percentile direction: the stored ``*_pct`` columns rank raw values ascending,
so for lower-is-better metrics the *displayed* percentile is inverted here —
every percentile on the site reads "higher = a better night".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config, metrics, score
from .schema import (
    CONFIDENCE_RATIONALE, LOW, SCORE_COMPONENTS, TOTAL_SCORE_WEIGHT,
    TRACKED_METRICS, Metric,
)

log = logging.getLogger("sleep.site")

DERIVED = "derived"
BADGE_RATIONALE = {**CONFIDENCE_RATIONALE,
                   DERIVED: "Computed from several measurements; inherits their confidence."}
BADGE_LABEL = {"high": "high confidence", "moderate": "moderate confidence",
               "low": "low confidence", DERIVED: "derived metric"}

PERCENTILE_LEGEND = ("Percentiles rank a night against every night ever recorded. "
                     "Higher is always better — so an early bedtime or a low resting "
                     "heart rate ranks high.")
CONFIDENCE_FOOTER = ("Confidence badges describe how well the ring can measure a "
                     "signal, not how good the value is.")

# Format kinds the JS formatter understands.
#   clock  decimal +24 hour -> h:mm am/pm      h1  hours, 1dp      pct0  percent
#   f0/f1  plain number                        f2s signed 2dp      int  thousands
FORMAT_BY_KEY = {
    "bedtime": "clock", "waketime": "clock",
    "total_sleep_h": "h1", "time_in_bed_h": "h1", "deep_h": "h1", "rem_h": "h1",
    "light_h": "h1", "nap_sleep_h": "h1", "sleep_debt_h": "h1",
    "sleep_need_h": "h1", "sleep_recommended_h": "h1",
    "efficiency": "pct0", "sleep_performance_pct": "pct0",
    "latency_min": "f0", "restless_periods": "f0",
    "hr_low": "f0", "hr_avg": "f0", "hrv": "f0", "steps": "int",
    "breaths_per_min": "f1", "temp_deviation": "f2s",
    "sleep_score": "f0", "sri": "f0", "readiness": "f0", "restfulness": "f0",
}


@dataclass(frozen=True)
class PageSpec:
    key: str
    label: str
    unit: str
    confidence: str
    higher_is_better: bool
    style: str            # 'scatter' (nightly points + trends) or 'line'
    group: str
    weight: float = 0.0
    extra_key: str | None = None   # companion series drawn on the same chart
    extra_label: str | None = None

    @property
    def slug(self) -> str:
        return self.key.replace("_", "-")

    @property
    def fmt(self) -> str:
        return FORMAT_BY_KEY.get(self.key, "f1")


def _component_spec(m: Metric) -> PageSpec:
    return PageSpec(m.key, m.label, m.unit, m.confidence, m.higher_is_better,
                    "scatter", "Score components", m.weight)


def _tracked_spec(m: Metric) -> PageSpec:
    return PageSpec(m.key, m.label, m.unit, m.confidence, m.higher_is_better,
                    "scatter", "Also tracked")


PAGES: list[PageSpec] = [
    PageSpec("sleep_score", "Sleep score", "score", DERIVED, True, "scatter", "Headline"),
    PageSpec("sleep_performance_pct", "Sleep performance", "%", DERIVED, True, "scatter", "Headline"),
    PageSpec("sleep_debt_h", "Sleep debt", "h", DERIVED, False, "line", "Headline"),
    PageSpec("sri", "Sleep regularity (SRI)", "score", DERIVED, True, "line", "Headline"),
    PageSpec("readiness", "Readiness", "score", DERIVED, True, "scatter", "Headline"),
    PageSpec("sleep_need_h", "Sleep need", "h", DERIVED, True, "line", "Headline",
             extra_key="sleep_recommended_h", extra_label="Recommended tonight"),
    # 'timing' is computed inside the score (c_timing) with no raw series of
    # its own to chart, so it appears as a score component, not a page.
    *[_component_spec(m) for m in SCORE_COMPONENTS if m.key != "timing"],
    *[_tracked_spec(m) for m in TRACKED_METRICS],
]

PAGE_BY_KEY = {p.key: p for p in PAGES}

# The eight overview cards (Q13: all of them).
CARD_KEYS = ["sleep_score", "sleep_performance_pct", "sleep_debt_h", "sri",
             "steps", "readiness", "temp_deviation", "breaths_per_min"]

JSON_BUDGET_KB = 300


# --- helpers ----------------------------------------------------------------

def _round(v, nd=2):
    if v is None or pd.isna(v):
        return None
    return round(float(v), nd)


def _display_pct(raw_pct, higher_is_better: bool):
    """Stored percentiles rank ascending; flip so higher always reads 'better'."""
    if raw_pct is None or pd.isna(raw_pct):
        return None
    pct = float(raw_pct) if higher_is_better else 100.0 - float(raw_pct)
    return round(pct, 1)


def _latest_row(daily: pd.DataFrame, key: str):
    valid = daily[daily[key].notna()]
    return None if valid.empty else valid.iloc[-1]


def _series_rows(daily: pd.DataFrame, spec: PageSpec) -> list[list]:
    key = spec.key
    cols = {"v": daily[key], "a7": daily.get(f"{key}_7d"),
            "a30": daily.get(f"{key}_30d")}
    frame = pd.DataFrame({k: c for k, c in cols.items() if c is not None})

    if spec.confidence == LOW:
        # Uncertainty band: the chart itself should say "treat loosely".
        std = daily[key].rolling(30, min_periods=10).std()
        frame["lo"] = frame["a30"] - std
        frame["hi"] = frame["a30"] + std
    if spec.extra_key and spec.extra_key in daily.columns:
        frame["extra"] = daily[spec.extra_key]

    first = frame["v"].first_valid_index()
    if first is None:
        return []
    frame = frame.loc[first:]
    rows = []
    for day, row in frame.iterrows():
        rows.append([day.strftime("%Y-%m-%d")] +
                    [_round(row.get(c)) for c in frame.columns])
    return rows


def _agg_payload(daily: pd.DataFrame, key: str) -> dict:
    out = {}
    for period in ("weekly", "quarterly", "annual"):
        agg = metrics.aggregate(daily, key, period)
        out[period] = [[ts.strftime("%Y-%m-%d"), _round(v), int(n)]
                       for ts, v, n in agg.itertuples(index=False)]
    return out


def _stats_payload(daily: pd.DataFrame, spec: PageSpec) -> dict:
    key, hib = spec.key, spec.higher_is_better
    last = _latest_row(daily, key)
    if last is None:
        return {}
    return {
        "day": last.name.strftime("%Y-%m-%d"),
        "value": _round(last[key]),
        "avg7": _round(last.get(f"{key}_7d")),
        "avg30": _round(last.get(f"{key}_30d")),
        "pct": _display_pct(last.get(f"{key}_pct"), hib),
        "pct7": _display_pct(last.get(f"{key}_pct_7d"), hib),
        "pct30": _display_pct(last.get(f"{key}_pct_30d"), hib),
    }


def _dist_payload(daily: pd.DataFrame, key: str) -> dict:
    """Where does the latest value sit in the whole distribution?"""
    s = daily[key].dropna()
    if s.empty:
        return {}
    return {"min": _round(s.min()), "p25": _round(s.quantile(.25)),
            "p50": _round(s.median()), "p75": _round(s.quantile(.75)),
            "max": _round(s.max()), "n": int(len(s))}


def _top_bottom_payload(daily: pd.DataFrame, spec: PageSpec) -> dict:
    end = daily.index.max()
    periods = {"all": None,
               "year": end - pd.Timedelta(days=365),
               "quarter": end - pd.Timedelta(days=90)}
    out = {}
    for name, since in periods.items():
        tb = metrics.top_bottom(daily, spec.key, n=10, since=since,
                                higher_is_better=spec.higher_is_better)
        out[name] = {
            side: [[ts.strftime("%Y-%m-%d"), _round(v)]
                   for ts, v in frame.itertuples(index=False)]
            for side, frame in tb.items() if not frame.empty
        }
    return out


# --- "how it's calculated" ---------------------------------------------------

def _comp(label, value, fmt, **extra) -> dict:
    return {"label": label, "value": _round(value), "format": fmt, **extra}


def _explain(daily: pd.DataFrame, spec: PageSpec) -> dict | None:
    """Explanation text + latest component values for derived metrics."""
    key = spec.key
    last = _latest_row(daily, key)
    if last is None:
        return None

    if key == "sleep_score":
        comps = []
        for m in SCORE_COMPONENTS:
            raw = last.get(m.key) if m.key in daily.columns else None
            comps.append(_comp(
                m.label, raw, FORMAT_BY_KEY.get(m.key, "f1"),
                score=_round(last.get(f"c_{m.key}"), 0), weight=m.weight,
                confidence=m.confidence,
                note="7-day bedtime consistency" if m.key == "timing" else None,
            ))
        return {
            "text": (
                "Each component is scored 0–100 against your own history — a "
                "percentile of every night you've recorded, or, for heart rate, "
                "HRV and breathing, against your trailing 90-day seasonal "
                "baseline. Sleep duration is the exception: it's scored against "
                "your sleep need. The score is the weighted average: "
                "Σ(weight × component) ÷ Σweight, with weights set by how "
                "reliably the ring can measure each signal. A missing component "
                "drops out of both sums rather than counting as zero."
            ),
            "formula": f"score = Σ(weight × component) ÷ {TOTAL_SCORE_WEIGHT:g}",
            "components": comps, "kind": "score",
        }

    if key == "sleep_performance_pct":
        slept = (last.get("total_sleep_h") or 0) + (last.get("nap_sleep_h") or 0)
        return {
            "text": ("How much of the sleep your body needed you actually got: "
                     "(night sleep + naps) ÷ sleep need, capped at 100%. Naps "
                     "count here because they genuinely repay sleep, even though "
                     "they're kept out of the sleep score."),
            "formula": "performance = (night + naps) ÷ need",
            "components": [
                _comp("Night sleep", last.get("total_sleep_h"), "h1"),
                _comp("Naps", last.get("nap_sleep_h"), "h1"),
                _comp("Sleep need", last.get("sleep_need_h"), "h1"),
                _comp("= Performance", last.get(key), "pct0", result=True),
            ],
        }

    if key == "sleep_debt_h":
        slept = (last.get("total_sleep_h") or 0) + (last.get("nap_sleep_h") or 0)
        need = last.get("sleep_need_h") or 0
        decay = float(np.exp(-1.0 / score.DEBT_TAU_DAYS))
        return {
            "text": (
                f"Accumulated shortfall against your sleep need, with old debt "
                f"fading: each night, debt = {decay:.3f} × yesterday's debt + "
                f"max(0, need − sleep). That decay means debt halves in about "
                f"{score.DEBT_TAU_DAYS * np.log(2):.1f} days if you sleep to need. "
                f"Naps count as sleep. A night with no recording holds debt where "
                f"it is — an unworn ring is not evidence you caught up."
            ),
            "formula": f"debt = {decay:.3f} × previous + max(0, need − slept)",
            "components": [
                _comp("Sleep need", need, "h1"),
                _comp("Slept (night + naps)", slept, "h1"),
                _comp("Last night's shortfall", max(0.0, need - slept), "h1"),
                _comp("= Debt now", last.get(key), "h1", result=True),
            ],
        }

    if key == "sleep_need_h":
        need = last.get("sleep_need_h")
        debt = last.get("sleep_debt_h") or 0
        rec = last.get("sleep_recommended_h")
        debt_up = min(debt * score.DEBT_UPLIFT_PER_HOUR, score.DEBT_UPLIFT_CAP_H)
        act_up = max(0.0, (rec or 0) - (need or 0) - debt_up)
        return {
            "text": (
                f"Your baseline need is the {int(score.NEED_QUANTILE * 100)}th "
                f"percentile of your own sleep over the trailing 180 days, kept "
                f"between {score.NEED_MIN_H:g} and {score.NEED_MAX_H:g} hours — "
                f"roughly what you sleep on your better nights, standing in for "
                f"unrestricted sleep. Debt and performance are measured against "
                f"this stable number. 'Recommended tonight' then adds repayment "
                f"of {int(score.DEBT_UPLIFT_PER_HOUR * 100)}% of current debt "
                f"(max {score.DEBT_UPLIFT_CAP_H:g}h) and "
                f"{int(score.ACTIVITY_UPLIFT_H * 60)} min after a top-quintile "
                f"step day. Keeping those uplifts out of the baseline avoids a "
                f"feedback loop where debt inflates the target it's measured "
                f"against."
            ),
            "formula": "recommended = need + min(0.15 × debt, 1h) + activity",
            "components": [
                _comp("Baseline need", need, "h1"),
                _comp("Debt repayment", debt_up, "h1"),
                _comp("Activity allowance", act_up, "h1"),
                _comp("= Recommended tonight", rec, "h1", result=True),
            ],
        }

    if key == "sri":
        window = daily.loc[:last.name].tail(30)
        return {
            "text": (
                "The Sleep Regularity Index: the probability that you're in the "
                "same state — asleep or awake — at the same clock minute on two "
                "consecutive days, over the trailing 30 days, rescaled to 0–100. "
                "100 means identical timing every night; 0 means no better than "
                "chance. It captures both when you sleep and how long, which a "
                "bedtime standard deviation misses, and low SRI is the regularity "
                "measure independently linked to health outcomes. Built from "
                "bedtime→waketime intervals (Oura's API doesn't expose minute-"
                "level sleep stages), so brief awakenings count as sleep and the "
                "number runs very slightly high — fine for comparing your own "
                "nights."
            ),
            "formula": "SRI = 200 × P(same state at same minute, day n vs n+1) − 100",
            "components": [
                _comp("Last bedtime", last.get("bedtime"), "clock"),
                _comp("Last waketime", last.get("waketime"), "clock"),
                _comp("Nights in 30-day window", window["bedtime"].notna().sum(), "f0"),
                _comp("= SRI", last.get(key), "f0", result=True),
            ],
        }

    if key == "readiness":
        comps = []
        for col, w, hib in score.READINESS_PARTS:
            if col == "__score__":
                comps.append(_comp("Last night's sleep score", last.get("sleep_score"),
                                   "f0", score=_round(last.get("sleep_score"), 0),
                                   weight=w))
                continue
            z = last.get(col)
            pct = score._z_to_percentile(pd.Series([z])).iloc[0] if pd.notna(z) else None
            good = None if pct is None else (pct if hib else 100 - pct)
            raw_key = col.replace("_z", "")
            comps.append(_comp(
                PAGE_BY_KEY[raw_key].label if raw_key in PAGE_BY_KEY else raw_key,
                last.get(raw_key), FORMAT_BY_KEY.get(raw_key, "f1"),
                score=_round(good, 0), weight=w,
                note="vs 90-day seasonal baseline" + ("" if hib else ", lower is better"),
            ))
        return {
            "text": (
                "How recovered your body looks this morning, as distinct from "
                "how you slept. HRV, resting heart rate and respiratory rate are "
                "each compared with your trailing 90-day baseline for this time "
                "of year and turned into a 0–100 score (higher HRV good; lower "
                "heart rate and breathing good), then blended with last night's "
                "sleep score: HRV 35%, resting HR 25%, breathing 15%, sleep "
                "score 25%. This replaces Oura's readiness with one whose inputs "
                "you can see."
            ),
            "formula": "readiness = 0.35·HRV + 0.25·HR + 0.15·breathing + 0.25·sleep score",
            "components": comps, "kind": "score",
        }
    return None


# --- payload + page emission -------------------------------------------------

def metric_payload(daily: pd.DataFrame, spec: PageSpec) -> dict:
    payload = {
        "meta": {
            "key": spec.key, "slug": spec.slug, "label": spec.label,
            "unit": spec.unit, "format": spec.fmt, "style": spec.style,
            "confidence": spec.confidence,
            "confidence_label": BADGE_LABEL[spec.confidence],
            "rationale": BADGE_RATIONALE[spec.confidence],
            "higher_is_better": spec.higher_is_better,
            "extra_label": spec.extra_label,
            "has_band": spec.confidence == LOW,
        },
        "series": _series_rows(daily, spec),
        "agg": _agg_payload(daily, spec.key),
        "stats": _stats_payload(daily, spec),
        "dist": _dist_payload(daily, spec.key),
        "top_bottom": _top_bottom_payload(daily, spec),
    }
    if spec.weight:
        c = daily.get(f"c_{spec.key}")
        latest_c = c.dropna().iloc[-1] if c is not None and c.notna().any() else None
        payload["meta"]["score_weight"] = spec.weight
        payload["meta"]["latest_contribution"] = _round(latest_c, 1)
    explain = _explain(daily, spec)
    if explain:
        payload["explain"] = explain
    return payload


def overview_payload(daily: pd.DataFrame, summary: dict) -> dict:
    cards = []
    for key in CARD_KEYS:
        spec = PAGE_BY_KEY[key]
        cards.append({"slug": spec.slug, "label": spec.label, "format": spec.fmt,
                      "confidence": spec.confidence, **_stats_payload(daily, spec)})
    recent = daily.iloc[-8:][::-1]
    flag_row = next((r for _, r in recent.iterrows()
                     if pd.notna(r.get("flag_raised"))), None)
    return {
        "latest_day": summary.get("latest", {}).get("day"),
        "cards": cards,
        "flag": {
            "raised": bool(flag_row["flag_raised"]) if flag_row is not None else False,
            "detail": (flag_row.get("flag_detail") or "") if flag_row is not None else "",
        },
        "nights": summary.get("nights_after_exclusions"),
        "range": summary.get("date_range"),
    }


# --- HTML --------------------------------------------------------------------

def _shell(title: str, body: str, root: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<link rel="icon" href="data:,">
<title>{title}</title>
<link rel="stylesheet" href="{root}/assets/site.css">
</head>
<body>
<nav class="nav">
  <a class="brand" href="{root}/index.html">Sleep</a>
  <a href="{root}/index.html">Overview</a>
  <a href="{root}/metrics/index.html">All metrics</a>
</nav>
{body}
<footer class="foot">
  <p>{PERCENTILE_LEGEND}</p>
  <p>{CONFIDENCE_FOOTER}</p>
  <p><a href="{root}/review/anomalies.md">Anomaly review</a> ·
  <a href="https://github.com/petedilworth/health-tracker">Repo</a></p>
</footer>
<script src="{root}/assets/plotly.min.js"></script>
<script src="{root}/assets/app.js"></script>
</body>
</html>"""


def _badge(spec: PageSpec) -> str:
    return (f'<span class="badge badge-{spec.confidence}" '
            f'title="{BADGE_RATIONALE[spec.confidence]}">'
            f'{BADGE_LABEL[spec.confidence]}</span>')


def _metric_page(spec: PageSpec) -> str:
    low_note = ('<p class="note">Sleep-stage inference is ~76–79% accurate '
                'against polysomnography — read this chart as directional, '
                'not exact. The shaded band shows ±1 SD of the trailing month.</p>'
                if spec.confidence == LOW else "")
    weight_line = (f'<p class="sub">Sleep-score component · weight {spec.weight} · '
                   f'<span id="contrib"></span></p>' if spec.weight else "")
    body = f"""
<main class="wrap">
  <header class="pagehead">
    <h1>{spec.label} {_badge(spec)}</h1>
    {weight_line}{low_note}
  </header>
  <section class="stats" id="stats"></section>
  <section class="card dist" id="dist"></section>
  <section class="card">
    <div class="controls">
      <div class="seg" id="view-toggle" role="tablist">
        <button data-view="daily" class="on">Daily</button>
        <button data-view="weekly">Weekly</button>
        <button data-view="quarterly">Quarterly</button>
        <button data-view="annual">Annual</button>
      </div>
    </div>
    <div id="chart" class="chart"></div>
  </section>
  <section class="card explain" id="explain" hidden></section>
  <section class="card">
    <div class="controls">
      <h2>Best &amp; worst nights</h2>
      <div class="seg" id="tb-toggle">
        <button data-period="all" class="on">All time</button>
        <button data-period="year">Last year</button>
        <button data-period="quarter">Last 90d</button>
      </div>
    </div>
    <div class="tb" id="tb"></div>
  </section>
</main>
<script>window.PAGE = {{type: "metric", slug: "{spec.slug}", root: ".."}};</script>"""
    return _shell(f"{spec.label} · Sleep Analytics", body, "..")


def _overview_page() -> str:
    body = """
<main class="wrap">
  <header class="pagehead"><h1 id="ov-title">Sleep Analytics</h1>
    <p class="sub" id="ov-sub"></p></header>
  <div id="flag"></div>
  <section class="cards" id="cards"></section>
  <section class="card">
    <div class="controls"><h2>Sleep score</h2></div>
    <div id="chart" class="chart chart-tall"></div>
  </section>
</main>
<script>window.PAGE = {type: "overview", root: "."};</script>"""
    return _shell("Sleep Analytics", body, ".")


def _all_metrics_page(daily: pd.DataFrame) -> str:
    groups: dict[str, list[str]] = {}
    for spec in PAGES:
        if spec.key not in daily.columns:
            continue
        stats = _stats_payload(daily, spec)
        val = stats.get("value")
        groups.setdefault(spec.group, []).append(
            f'<a class="mrow" href="{spec.slug}.html"><span>{spec.label}</span>'
            f'{_badge(spec)}<span class="mval" data-fmt="{spec.fmt}" '
            f'data-val="{"" if val is None else val}"></span></a>'
        )
    sections = "".join(
        f'<h2>{name}</h2><div class="mlist">{"".join(rows)}</div>'
        for name, rows in groups.items()
    )
    body = f"""
<main class="wrap">
  <header class="pagehead"><h1>All metrics</h1></header>
  {sections}
</main>
<script>window.PAGE = {{type: "list", root: ".."}};</script>"""
    return _shell("All metrics · Sleep Analytics", body, "..")


# --- build -------------------------------------------------------------------

def build_site(daily: pd.DataFrame, summary: dict) -> None:
    docs = config.DOCS_DIR
    (docs / "data" / "m").mkdir(parents=True, exist_ok=True)
    (docs / "metrics").mkdir(parents=True, exist_ok=True)

    for spec in PAGES:
        if spec.key not in daily.columns:
            log.warning("Skipping %s — column missing", spec.key)
            continue
        payload = metric_payload(daily, spec)
        path = docs / "data" / "m" / f"{spec.slug}.json"
        path.write_text(json.dumps(payload, separators=(",", ":")),
                        encoding="utf-8")
        size_kb = path.stat().st_size / 1024
        if size_kb > JSON_BUDGET_KB:
            log.warning("%s payload is %.0f KB (budget %d)", spec.slug,
                        size_kb, JSON_BUDGET_KB)
        (docs / "metrics" / f"{spec.slug}.html").write_text(
            _metric_page(spec), encoding="utf-8")

    (docs / "data" / "overview.json").write_text(
        json.dumps(overview_payload(daily, summary), separators=(",", ":")),
        encoding="utf-8")
    (docs / "index.html").write_text(_overview_page(), encoding="utf-8")
    (docs / "metrics" / "index.html").write_text(_all_metrics_page(daily),
                                                 encoding="utf-8")
    (docs / "robots.txt").write_text("User-agent: *\nDisallow: /\n",
                                     encoding="utf-8")
    log.info("Site built -> %s (%d metric pages)", docs, len(PAGES))
