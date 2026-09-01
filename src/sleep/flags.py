"""The combined health flag.

Body temperature and respiratory rate are early-warning signals, not sleep
quality signals — a spike means something is happening to you (illness, alcohol,
a hard day), not that you slept badly. So they stay out of the sleep score and
instead drive one banner that appears only when something has actually moved.

One combined flag rather than three independent ones: on a normal morning you
should see nothing at all, and silence is then meaningful.
"""

from __future__ import annotations

import pandas as pd

# In SDs above the seasonal baseline. At 1.5 SD the flag fired on 11% of nights
# in this dataset — roughly one morning in nine, which is often enough that it
# would be tuned out and stop being read. At 2.0 SD it lands nearer 4%, which
# keeps "something's off" worth looking at.
THRESHOLD_Z = 2.0

WATCHED = [
    ("temp_deviation", "Body temperature", "°C", True),
    ("breaths_per_min", "Respiratory rate", "/min", True),
]


def health_flags(daily: pd.DataFrame) -> pd.DataFrame:
    """Per-night flag state plus the contributing detail.

    Columns: flag_raised (bool), flag_detail (str), flag_severity (max |z|).
    """
    raised = pd.Series(False, index=daily.index)
    severity = pd.Series(0.0, index=daily.index)
    details: list[list[str]] = [[] for _ in range(len(daily))]

    for col, label, unit, elevated_is_bad in WATCHED:
        z_col = f"{col}_z"
        if z_col not in daily.columns:
            continue
        z = daily[z_col]
        hit = (z >= THRESHOLD_Z) if elevated_is_bad else (z.abs() >= THRESHOLD_Z)
        hit = hit.fillna(False)
        raised |= hit
        severity = severity.combine(z.abs().fillna(0), max)

        values = daily[col]
        for pos in range(len(daily)):
            if hit.iloc[pos]:
                details[pos].append(
                    f"{label} {values.iloc[pos]:+.2f}{unit} "
                    f"({z.iloc[pos]:+.1f} SD vs baseline)"
                )

    return pd.DataFrame({
        "flag_raised": raised,
        "flag_detail": ["; ".join(d) for d in details],
        "flag_severity": severity.where(raised, 0.0),
    }, index=daily.index)
