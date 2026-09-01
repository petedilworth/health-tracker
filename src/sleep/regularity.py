"""Sleep Regularity Index (SRI).

SRI is the probability of being in the same state (asleep or awake) at the same
clock minute on two consecutive days, rescaled to -100..100 — though in practice
it sits 0..100 for anyone with a roughly daily rhythm. 100 means perfectly
identical timing every day; 0 means no better than chance.

It is the regularity measure with published links to health outcomes, and it
captures both *when* you sleep and *how long*, which a bedtime standard
deviation does not.

Limitation worth stating plainly: Oura's API exposes sleep *intervals*, not
epoch-level hypnograms, so the asleep/awake series is reconstructed from
bedtime -> waketime. Brief awakenings inside the night are therefore counted as
sleep, and naps (whose clock times the API doesn't give us in this schema) are
omitted. That makes this a close approximation of published SRI, biased very
slightly high, and entirely valid for comparing your own nights to each other.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MINUTES_PER_DAY = 1440
DEFAULT_WINDOW_DAYS = 30
# SRI is built from *consecutive-day pairs*, and a pair needs both days present.
# Pairs are therefore much scarcer than nights: at a 60% wear rate only ~36% of
# day-pairs are usable. Demanding 60% of the window as pairs silently blanked
# SRI across every patchy stretch, so the bar is a third of the window.
MIN_WINDOW_COVERAGE = 0.33


def build_sleep_matrix(daily: pd.DataFrame) -> np.ndarray:
    """(n_days x 1440) bool array: was I asleep at this minute of this day?

    `bedtime` and `waketime` are decimal hours on the +24 convention (1am =
    25.0), measured from midnight starting the labelled day, so an interval
    naturally spills into the following row.
    """
    n = len(daily)
    grid = np.zeros(n * MINUTES_PER_DAY, dtype=bool)
    if n == 0:
        return grid.reshape(0, MINUTES_PER_DAY)

    bed = daily["bedtime"].to_numpy(dtype=float)
    wake = daily["waketime"].to_numpy(dtype=float)
    for i in range(n):
        if not (np.isfinite(bed[i]) and np.isfinite(wake[i])) or wake[i] <= bed[i]:
            continue
        start = int(round(i * MINUTES_PER_DAY + bed[i] * 60))
        end = int(round(i * MINUTES_PER_DAY + wake[i] * 60))
        start, end = max(0, start), min(len(grid), end)
        if end > start:
            grid[start:end] = True
    return grid.reshape(n, MINUTES_PER_DAY)


def sleep_regularity_index(daily: pd.DataFrame,
                           window: int = DEFAULT_WINDOW_DAYS) -> pd.Series:
    """Trailing-window SRI per day, 0-100 (theoretically -100..100)."""
    if daily.empty:
        return pd.Series(dtype=float)

    matrix = build_sleep_matrix(daily)
    has_data = daily["bedtime"].notna().to_numpy()

    # agreement[i] = share of minutes where day i and day i+1 match.
    agreement = (matrix[:-1] == matrix[1:]).mean(axis=1)
    # Only day-pairs where both days actually have a recording are meaningful.
    valid_pair = has_data[:-1] & has_data[1:]
    agreement = np.where(valid_pair, agreement, np.nan)

    # Label each pair by its *second* day, so a window ending today is built
    # only from days up to today — never from a day that hasn't happened yet.
    pair_series = pd.Series(agreement, index=daily.index[1:])
    min_pairs = max(3, int(window * MIN_WINDOW_COVERAGE))
    rolled = pair_series.rolling(window, min_periods=min_pairs).mean()

    sri = 200.0 * rolled - 100.0
    # Align to the day the window ends on, and keep the last day present.
    return sri.reindex(daily.index).clip(lower=-100, upper=100)
