#!/usr/bin/env python3
"""Render a dashboard from synthetic data to eyeball the visualization.

Not part of the pipeline — a dev helper so the charts can be checked without a
live Oura token. Writes images/dashboard_demo.png.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oura import processing, viz  # noqa: E402

rng = np.random.default_rng(42)
n = 400
days = pd.date_range("2024-01-01", periods=n).date
# Sleep score: noisy around a slow upward trend, clipped to 0-100.
score = np.clip(72 + np.linspace(-6, 6, n) + rng.normal(0, 7, n), 30, 100).round()
# Bedtime: mostly ~23:15 with occasional after-midnight nights (24-25.5).
bedtime = np.clip(23.2 + rng.normal(0, 0.8, n), 21.5, 26.0).round(2)

raw = pd.DataFrame({"day": days, "bedtime": bedtime, "score": score})
full = processing.add_rolling_and_percentiles(raw)
out = viz.save_dashboard(full, Path(__file__).resolve().parents[1] / "images" / "dashboard_demo.png")
print("Wrote", out)
