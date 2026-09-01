"""Static site generator: computed frame -> docs/ for GitHub Pages.

Python does all the shaping (formatting rules, percentile direction, aggregates,
top/bottom lists, sparklines) so the browser-side JS stays a thin chart driver.
Pages are plain HTML from f-string templates; data ships as JSON per metric.

Percentile direction: the stored ``*_pct`` columns rank raw values ascending,
so for lower-is-better metrics the *displayed* percentile is inverted here —
every number on the site reads "higher percentile = better night".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import pandas as pd

from . import config, metrics
from .schema import (
    CONFIDENCE_RATIONALE, LOW, SCORE_COMPONENTS, TRACKED_METRICS, Metric,
)

log = logging.getLogger("sleep.site")

DERIVED = "derived"
BADGE_RATIONALE = {**CONFIDENCE_RATIONALE,
                   DERIVED: "Computed from several measurements; inherits their confidence."}

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

SPARK_DAYS = 90
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
    valid = daily[daily[key].notna()]
    if valid.empty:
        return {}
    last = valid.iloc[-1]
    return {
        "day": valid.index[-1].strftime("%Y-%m-%d"),
        "value": _round(last[key]),
        "avg7": _round(last.get(f"{key}_7d")),
        "avg30": _round(last.get(f"{key}_30d")),
        "pct": _display_pct(last.get(f"{key}_pct"), hib),
        "pct7": _display_pct(last.get(f"{key}_pct_7d"), hib),
        "pct30": _display_pct(last.get(f"{key}_pct_30d"), hib),
    }


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


def _sparkline_svg(values: list[float | None], w=120, h=32) -> str:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return f'<svg class="spark" viewBox="0 0 {w} {h}"></svg>'
    xs, ys = zip(*pts)
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1.0
    step = w / max(len(values) - 1, 1)
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x * step:.1f},{h - 3 - (y - lo) / span * (h - 6):.1f}"
        for i, (x, y) in enumerate(pts)
    )
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<path d="{path}"/></svg>')


# --- payload + page emission -------------------------------------------------

def metric_payload(daily: pd.DataFrame, spec: PageSpec) -> dict:
    payload = {
        "meta": {
            "key": spec.key, "slug": spec.slug, "label": spec.label,
            "unit": spec.unit, "format": spec.fmt, "style": spec.style,
            "confidence": spec.confidence,
            "rationale": BADGE_RATIONALE[spec.confidence],
            "higher_is_better": spec.higher_is_better,
            "extra_label": spec.extra_label,
            "has_band": spec.confidence == LOW,
        },
        "series": _series_rows(daily, spec),
        "agg": _agg_payload(daily, spec.key),
        "stats": _stats_payload(daily, spec),
        "top_bottom": _top_bottom_payload(daily, spec),
    }
    if spec.weight:
        c = daily.get(f"c_{spec.key}")
        latest_c = c.dropna().iloc[-1] if c is not None and c.notna().any() else None
        payload["meta"]["score_weight"] = spec.weight
        payload["meta"]["latest_contribution"] = _round(latest_c, 1)
    return payload


def overview_payload(daily: pd.DataFrame, summary: dict) -> dict:
    cards = []
    for key in CARD_KEYS:
        spec = PAGE_BY_KEY[key]
        stats = _stats_payload(daily, spec)
        spark = daily[key].tail(SPARK_DAYS)
        cards.append({
            "slug": spec.slug, "label": spec.label, "format": spec.fmt,
            "confidence": spec.confidence, **stats,
            "spark": [_round(v) for v in spark.tolist()],
        })
    latest_flag = daily.iloc[-8:][::-1]  # most recent night with any flag info
    flag_row = next((r for _, r in latest_flag.iterrows()
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
<title>{title}</title>
<link rel="icon" href="data:,">
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
  <a href="{root}/review/anomalies.md">Anomaly review</a> ·
  <a href="https://github.com/petedilworth/health-tracker">Repo</a>
</footer>
<script src="{root}/assets/plotly.min.js"></script>
<script src="{root}/assets/app.js"></script>
</body>
</html>"""


def _badge(spec: PageSpec) -> str:
    return (f'<span class="badge badge-{spec.confidence}" '
            f'title="{BADGE_RATIONALE[spec.confidence]}">{spec.confidence}</span>')


def _metric_page(spec: PageSpec) -> str:
    low_note = ('<p class="note">Sleep-stage inference is ~76–79% accurate '
                'against polysomnography — read this chart as directional, '
                'not exact. The shaded band shows ±1 SD of the trailing month.</p>'
                if spec.confidence == LOW else "")
    weight_line = (f'<p class="sub">Score component · weight {spec.weight} · '
                   f'<span id="contrib"></span></p>' if spec.weight else "")
    body = f"""
<main class="wrap">
  <header class="pagehead">
    <h1>{spec.label} {_badge(spec)}</h1>
    {weight_line}{low_note}
  </header>
  <section class="stats" id="stats"></section>
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
