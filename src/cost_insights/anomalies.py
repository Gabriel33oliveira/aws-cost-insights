"""Daily cost anomaly detection.

``detect_anomalies`` looks for sudden daily cost jumps using a trailing
rolling median and median absolute deviation (MAD), which are robust to the
very spikes the detector is looking for. It takes the normalized DataFrame
returned by ``cost_insights.loader.load_cur``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

ANOMALY_DIMENSIONS = ("service", "account_id", "region")
TOTAL_KEY = "(total)"
MIN_HISTORY_DAYS = 7
# Scales the MAD so it estimates the standard deviation of normal data.
MAD_SCALE = 1.4826
# A nonzero MAD is raised to at least this fraction of the median, so a very
# smooth history does not turn ordinary jitter of a few percent into a high score.
MIN_MAD_FRACTION = 0.05
# When the MAD is zero, a day is flagged if its cost exceeds this multiple of the median.
ZERO_MAD_FACTOR = 2.0


@dataclass(frozen=True)
class Anomaly:
    """A day whose cost for one key jumped well above its recent baseline."""

    date: date
    dimension: str | None
    key: str
    cost: float
    baseline: float
    pct_change: float
    score: float


def detect_anomalies(
    df: pd.DataFrame,
    by: str | None = "service",
    window: int = 14,
    threshold: float = 3.0,
    min_cost: float = 10.0,
) -> list[Anomaly]:
    """Flag days whose cost jumps well above the trailing baseline.

    Daily cost is summed per key of ``by`` (``service``, ``account_id``,
    ``region``, or ``None`` for the overall total). Days with no spend for a
    key count as 0. For each day, the baseline is the median of the previous
    ``window`` days (the current day excluded) and the spread is their MAD.
    A day is flagged when its robust z-score, ``(cost - median) / (1.4826 *
    MAD)``, exceeds ``threshold``. A nonzero MAD is floored at 5% of the
    median so tiny day-to-day jitter on a smooth series is not flagged. When
    the MAD is zero, a day is flagged when cost exceeds twice the median. Days need a cost of at least ``min_cost``
    and at least 7 prior days of history.

    ``score`` is the robust z-score, or ``cost / median`` when the MAD is zero
    (infinite if the median is zero too). ``pct_change`` is the change versus
    the baseline as a fraction (NaN when the baseline is zero). Results are
    sorted by score, highest first.
    """
    if by is not None and by not in ANOMALY_DIMENSIONS:
        raise ValueError(
            f"Unsupported dimension {by!r}; use one of {', '.join(ANOMALY_DIMENSIONS)} or None"
        )
    if window < MIN_HISTORY_DAYS:
        raise ValueError(f"window must be at least {MIN_HISTORY_DAYS}")
    if df.empty:
        return []

    dates = df["usage_date"].dt.normalize()
    keys = df[by].astype(str) if by is not None else pd.Series(TOTAL_KEY, index=df.index)
    daily = (
        df["cost"]
        .groupby([dates.rename("date"), keys.rename("key")])
        .sum()
        .unstack("key", fill_value=0.0)
    )
    all_days = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(all_days, fill_value=0.0)

    anomalies: list[Anomaly] = []
    for key in daily.columns:
        series = daily[key]
        history = series.shift(1).rolling(window, min_periods=MIN_HISTORY_DAYS)
        median = history.median()
        # The shifted series starts with NaN, so early windows must skip it.
        mad = history.apply(lambda w: np.nanmedian(np.abs(w - np.nanmedian(w))), raw=True)
        for day, cost, base, spread in zip(series.index, series, median, mad):
            if np.isnan(base) or cost < min_cost:
                continue
            if spread > 0:
                spread = max(spread, MIN_MAD_FRACTION * abs(base))
                score = (cost - base) / (MAD_SCALE * spread)
                flagged = score > threshold
            else:
                score = cost / base if base > 0 else float("inf")
                flagged = cost > ZERO_MAD_FACTOR * base
            if not flagged:
                continue
            anomalies.append(
                Anomaly(
                    date=day.date(),
                    dimension=by,
                    key=str(key),
                    cost=float(cost),
                    baseline=float(base),
                    pct_change=float((cost - base) / base) if base else float("nan"),
                    score=float(score),
                )
            )

    anomalies.sort(key=lambda a: (-a.score, a.date, a.key))
    return anomalies
