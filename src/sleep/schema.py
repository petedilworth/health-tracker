"""Canonical column definitions and measurement-confidence tiers.

Confidence tiers come from independent validation of the Oura ring against
polysomnography (not Oura's own marketing). They drive two things: how heavily a
metric is weighted in the sleep score, and how the website renders it.

  HIGH     Direct sensor measurement (PPG, thermistor) or sleep/wake timing,
           which wearables detect with 94%+ sensitivity.
  MODERATE Derived from wake detection or proprietary movement processing.
           Wake specificity is only 29-52%, so efficiency is systematically
           overestimated (+1.75-7.9%) and range-compressed near the top.
  LOW      Sleep-stage inference. Four-stage agreement with PSG is ~76-79% for
           Oura; most wrist wearables manage only 60-65%.

References:
  https://mhealth.jmir.org/2023/1/e50983
  https://academic.oup.com/sleepadvances/article/6/2/zpaf021/8090472
  https://pmc.ncbi.nlm.nih.gov/articles/PMC11511193/
  https://www.sciencedirect.com/science/article/pii/S1389945724000200
"""

from __future__ import annotations

from dataclasses import dataclass

HIGH = "high"
MODERATE = "moderate"
LOW = "low"

CONFIDENCE_RATIONALE = {
    HIGH: "Direct sensor measurement or sleep/wake timing (94%+ sensitivity vs PSG).",
    MODERATE: (
        "Derived from wake detection or proprietary movement processing. Wake "
        "specificity is only 29-52%, so values skew optimistic and compress."
    ),
    LOW: (
        "Sleep-stage inference. Four-stage agreement with polysomnography is "
        "~76-79%; treat night-to-night changes as directional, not exact."
    ),
}


@dataclass(frozen=True)
class Metric:
    key: str          # column name in history.csv
    label: str        # human-readable name
    unit: str
    confidence: str
    # Weight in the composite sleep score; 0.0 means "tracked but not scored".
    weight: float = 0.0
    # True when higher values are better (drives top/bottom lists and colouring).
    higher_is_better: bool = True


# --- Sleep score components -------------------------------------------------
# All components are scored RELATIVE to personal baseline. A roughly constant
# measurement bias cancels out when comparing you against your own history,
# which is what makes the low-confidence components usable at all.
SCORE_COMPONENTS: list[Metric] = [
    Metric("bedtime", "Bedtime", "clock", HIGH, 1.0, higher_is_better=False),
    Metric("time_in_bed_h", "Time in bed", "h", HIGH, 1.0),
    Metric("timing", "Timing consistency", "score", HIGH, 1.0),
    Metric("hr_low", "Lowest heart rate", "bpm", HIGH, 1.0, higher_is_better=False),
    Metric("hrv", "HRV", "ms", HIGH, 1.0),
    Metric("breaths_per_min", "Respiratory rate", "/min", HIGH, 1.0,
           higher_is_better=False),
    Metric("efficiency", "Sleep efficiency", "%", MODERATE, 0.5),
    Metric("restfulness", "Restfulness", "score", MODERATE, 0.5),
    Metric("rem_h", "REM sleep", "h", LOW, 0.25),
    Metric("deep_h", "Deep sleep", "h", LOW, 0.25),
]

TOTAL_SCORE_WEIGHT = sum(m.weight for m in SCORE_COMPONENTS)  # 7.5

# --- Tracked but not scored -------------------------------------------------
TRACKED_METRICS: list[Metric] = [
    Metric("total_sleep_h", "Total sleep", "h", HIGH),
    Metric("light_h", "Light sleep", "h", LOW),
    Metric("latency_min", "Sleep latency", "min", MODERATE, higher_is_better=False),
    Metric("restless_periods", "Restless periods", "count", MODERATE,
           higher_is_better=False),
    Metric("hr_avg", "Average heart rate", "bpm", HIGH, higher_is_better=False),
    Metric("temp_deviation", "Temperature deviation", "°C", HIGH),
    Metric("steps", "Steps", "steps", HIGH),
    Metric("nap_sleep_h", "Nap sleep", "h", HIGH),
]

ALL_METRICS: list[Metric] = SCORE_COMPONENTS + TRACKED_METRICS
METRICS_BY_KEY: dict[str, Metric] = {m.key: m for m in ALL_METRICS}

# --- Raw stored columns -----------------------------------------------------
# Derived values (rolling averages, percentiles, the score itself) are NOT
# stored: they are recomputed over full history on every run so they are always
# current and consistent with the latest exclusions.
RAW_COLUMNS: list[str] = [
    "day",
    # sleep session (long_sleep only)
    "bedtime", "waketime", "total_sleep_h", "time_in_bed_h", "efficiency",
    "latency_min", "restless_periods", "deep_h", "rem_h", "light_h",
    "hr_low", "hr_avg", "hrv", "breaths_per_min",
    # naps (summed separately; pay down debt but never scored)
    "nap_sleep_h",
    # daily_activity
    "steps",
    # daily_readiness
    "temp_deviation", "temp_trend_deviation",
    # Oura's own scores — stored for comparison, not displayed (Q6)
    "oura_sleep_score", "oura_readiness_score", "restfulness",
]

EXCLUSION_COLUMNS: list[str] = ["day", "reason", "added_at"]
