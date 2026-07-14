"""Render the analytics DataFrame into a single dashboard PNG.

Chart design follows the project's data-viz method:
  * Forms are chosen by job: trends over time = line + scatter; distributions =
    histograms; the KPI summary is a table (a grid of numbers, not a chart).
  * Colours are a small, fixed, colourblind-safe categorical set — assigned to
    the daily / 7-day / 30-day series in that order and never cycled.
  * "Today" is always the red highlight so the eye finds it instantly.
Bucket sizes: 1 point for sleep score, 0.5 hour for bedtime.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend — no display on CI runners
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec

# --- Palette (validated, light surface) -------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
C_DAILY = "#2a78d6"   # blue   — slot 1
C_7D = "#1baf7a"      # aqua   — slot 2
C_30D = "#eb6834"     # orange — slot 8
C_TODAY = "#e34948"   # red    — the "today" highlight
C_GOOD = "#0ca30c"    # percentile >= 70
C_BAD = "#d03b3b"     # percentile <= 30

# Per-metric display config keeps the plotting code DRY (one loop, no repeats).
METRIC_CONFIG = {
    "score": {"label": "Sleep Score", "unit": "", "bucket": 1.0, "fmt": "{:.0f}"},
    "bedtime": {"label": "Bedtime", "unit": "h", "bucket": 0.5, "fmt": "{:.1f}"},
}


def _bedtime_label(decimal_hour: float) -> str:
    """25.5 -> '1:30am' for human-readable bedtime display."""
    h = decimal_hour % 24
    hour = int(h)
    minute = int(round((h - hour) * 60))
    if minute == 60:
        hour, minute = (hour + 1) % 24, 0
    suffix = "am" if hour < 12 else "pm"
    disp_hour = hour % 12 or 12
    return f"{disp_hour}:{minute:02d}{suffix}"


def _fmt_value(metric: str, value: float) -> str:
    if pd.isna(value):
        return "—"
    if metric == "bedtime":
        return _bedtime_label(value)
    return METRIC_CONFIG[metric]["fmt"].format(value)


def _style_axis(ax) -> None:
    ax.set_facecolor(SURFACE)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)


def _plot_trend(ax, df: pd.DataFrame, metric: str) -> None:
    cfg = METRIC_CONFIG[metric]
    days = pd.to_datetime(df["day"])
    ax.scatter(days, df[metric], s=10, color=C_DAILY, alpha=0.35, label="Daily",
               zorder=2, edgecolors="none")
    ax.plot(days, df[f"{metric}_7d"], color=C_7D, linewidth=2, label="7-day avg",
            zorder=3)
    ax.plot(days, df[f"{metric}_30d"], color=C_30D, linewidth=2, label="30-day avg",
            zorder=3)
    # Most recent point in red so the latest night stands out.
    ax.scatter(days.iloc[-1], df[metric].iloc[-1], s=45, color=C_TODAY, zorder=5,
               edgecolors=SURFACE, linewidths=1.5)
    _style_axis(ax)
    ax.set_title(f"{cfg['label']} — trend", fontsize=10, color=INK, loc="left")
    ax.legend(loc="best", fontsize=7, framealpha=0.9, edgecolor=GRID)
    # Keep date ticks sparse so labels never collide in narrow subplots.
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    if metric == "bedtime":
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: _bedtime_label(v))
        )


def _plot_hist(ax, df: pd.DataFrame, metric: str, col: str, title: str) -> None:
    cfg = METRIC_CONFIG[metric]
    series = df[col].dropna()
    if series.empty:
        return
    bucket = cfg["bucket"]
    bins = np.arange(series.min(), series.max() + bucket, bucket)
    ax.hist(series, bins=bins, color=C_DAILY, alpha=0.75, edgecolor=SURFACE,
            linewidth=0.5)
    # Red dashed line marks today's value on the distribution.
    today = df[col].iloc[-1]
    if pd.notna(today):
        ax.axvline(today, color=C_TODAY, linestyle="--", linewidth=1.8, zorder=5)
    _style_axis(ax)
    ax.set_title(title, fontsize=8.5, color=INK, loc="left")
    if metric == "bedtime":
        ax.xaxis.set_major_formatter(
            plt.FuncFormatter(lambda v, _: _bedtime_label(v))
        )


def _render_kpi_table(ax, df: pd.DataFrame) -> None:
    ax.axis("off")
    latest = df.iloc[-1]
    headers = ["Metric", "Today", "7d avg", "30d avg",
               "%ile (day)", "%ile (7d)", "%ile (30d)"]
    rows, pct_cells = [], []
    for metric, cfg in METRIC_CONFIG.items():
        if metric not in df.columns:
            continue
        rows.append([
            cfg["label"],
            _fmt_value(metric, latest[metric]),
            _fmt_value(metric, latest[f"{metric}_7d"]),
            _fmt_value(metric, latest[f"{metric}_30d"]),
            f"{latest[f'{metric}_pct_daily']:.0f}",
            f"{latest[f'{metric}_pct_7d']:.0f}",
            f"{latest[f'{metric}_pct_30d']:.0f}",
        ])
        pct_cells.append([latest[f"{metric}_pct_daily"],
                          latest[f"{metric}_pct_7d"],
                          latest[f"{metric}_pct_30d"]])

    table = ax.table(cellText=rows, colLabels=headers, loc="center",
                     cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.6)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if r == 0:
            cell.set_text_props(color=INK, fontweight="bold")
            cell.set_facecolor("#f0efec")
        elif c >= 4:  # percentile columns get green/red conditional colour
            pct = pct_cells[r - 1][c - 4]
            if pct >= 70:
                cell.set_text_props(color=C_GOOD, fontweight="bold")
            elif pct <= 30:
                cell.set_text_props(color=C_BAD, fontweight="bold")
    ax.set_title("KPI summary (percentile vs. all history)", fontsize=10,
                 color=INK, loc="left", pad=12)


def generate_dashboard(df: pd.DataFrame) -> plt.Figure:
    """Build the full 4-band dashboard figure from the analytics DataFrame."""
    latest_day = pd.to_datetime(df["day"].iloc[-1]).strftime("%A, %d %b %Y")
    fig = plt.figure(figsize=(12, 13), facecolor=SURFACE)
    # 6 columns so trends split into equal halves and histograms into thirds.
    gs = GridSpec(4, 6, figure=fig, height_ratios=[1.3, 1, 1, 0.8],
                  hspace=0.45, wspace=0.55)

    # Band 1: trends — one per metric, equal halves.
    _plot_trend(fig.add_subplot(gs[0, 0:3]), df, "score")
    _plot_trend(fig.add_subplot(gs[0, 3:6]), df, "bedtime")

    # Bands 2 & 3: histograms (daily / 7d / 30d) for each metric, equal thirds.
    for band, metric in ((1, "score"), (2, "bedtime")):
        for col, (suffix, title) in enumerate([
            ("", "daily"), ("_7d", "7-day avg"), ("_30d", "30-day avg")
        ]):
            _plot_hist(fig.add_subplot(gs[band, 2 * col:2 * col + 2]), df, metric,
                       f"{metric}{suffix}",
                       f"{METRIC_CONFIG[metric]['label']} — {title}")

    # Band 4: KPI table.
    _render_kpi_table(fig.add_subplot(gs[3, :]), df)

    fig.suptitle(f"Oura Sleep Analytics  ·  {latest_day}", fontsize=15,
                 color=INK, x=0.02, ha="left", fontweight="bold")
    return fig


def save_dashboard(df: pd.DataFrame, output_path: Path) -> Path:
    """Render and save the dashboard PNG, then free the figure's memory."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig = generate_dashboard(df)
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return output_path
