"""Spend breakdowns, trends, top drivers and untagged spend.

Every function here is pure: it takes the normalized DataFrame returned by
``cost_insights.loader.load_cur`` and returns a new DataFrame or dict without
modifying its input.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

UNTAGGED = "(untagged)"
TAG_DIMENSION_PREFIX = "tag:"
BASE_DIMENSIONS = ("service", "account_id", "region")
DRIVER_KEYS = ["service", "account_id", "region"]


def total_spend(df: pd.DataFrame) -> float:
    """Return the sum of ``cost`` over all rows."""
    return float(df["cost"].sum())


def spend_by(df: pd.DataFrame, dimension: str, top: int | None = None) -> pd.DataFrame:
    """Break spend down by one dimension.

    ``dimension`` is ``service``, ``account_id``, ``region`` or ``tag:<key>``.
    Returns columns ``key``, ``cost`` and ``share`` (fraction of total spend),
    sorted by cost descending. Rows with no value for a tag dimension are
    grouped under ``(untagged)``. ``top`` keeps only the first N rows; shares
    are still relative to the full total.
    """
    keys = _dimension_values(df, dimension)
    grouped = df["cost"].groupby(keys).sum()
    result = grouped.rename_axis("key").reset_index(name="cost")
    total = total_spend(df)
    result["share"] = result["cost"] / total if total else np.nan
    result = result.sort_values(["cost", "key"], ascending=[False, True], kind="stable")
    if top is not None:
        result = result.head(top)
    return result.reset_index(drop=True)


def monthly_trend(df: pd.DataFrame, dimension: str | None = None) -> pd.DataFrame:
    """Return per-month totals with change versus the previous month.

    Columns are ``month`` (``YYYY-MM``), ``cost``, ``change`` (absolute) and
    ``pct_change`` (fraction, e.g. 0.05 for +5%). The first month, and any
    month whose previous cost is zero, has NaN for ``pct_change``; the first
    month also has NaN for ``change``. With ``dimension``, a ``key`` column is
    added and the trend is computed per key, counting months without spend as 0.
    """
    months = df["usage_date"].dt.to_period("M")
    if dimension is None:
        monthly = df["cost"].groupby(months).sum().sort_index()
        result = _with_changes(monthly).rename_axis("month").reset_index()
        result["month"] = result["month"].astype(str)
        return result[["month", "cost", "change", "pct_change"]]

    keys = _dimension_values(df, dimension)
    pivot = df["cost"].groupby([keys.rename("key"), months.rename("month")]).sum().unstack(
        "month", fill_value=0.0
    )
    pivot = pivot.reindex(columns=sorted(pivot.columns))
    frames = []
    for key, row in pivot.iterrows():
        frame = _with_changes(row).rename_axis("month").reset_index()
        frame.insert(0, "key", key)
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    result["month"] = result["month"].astype(str)
    return result[["key", "month", "cost", "change", "pct_change"]]


def top_drivers(df: pd.DataFrame, top: int = 5) -> pd.DataFrame:
    """Rank (service, account_id, region) by cost increase over the last two months.

    Returns columns ``service``, ``account_id``, ``region``, ``previous_cost``,
    ``current_cost`` and ``delta``, sorted by ``delta`` descending. Combinations
    missing from one of the months count as 0 there. If the data covers fewer
    than two months the result is empty.
    """
    columns = DRIVER_KEYS + ["previous_cost", "current_cost", "delta"]
    months = df["usage_date"].dt.to_period("M")
    periods = sorted(months.unique())
    if len(periods) < 2:
        return pd.DataFrame(columns=columns)

    previous, current = periods[-2], periods[-1]
    recent = df[months.isin([previous, current])]
    pivot = (
        recent.groupby(DRIVER_KEYS + [months[recent.index].rename("month")])["cost"]
        .sum()
        .unstack("month", fill_value=0.0)
        .reindex(columns=[previous, current], fill_value=0.0)
    )
    result = pd.DataFrame(
        {
            "previous_cost": pivot[previous],
            "current_cost": pivot[current],
        }
    )
    result["delta"] = result["current_cost"] - result["previous_cost"]
    result = result.reset_index()
    result = result.sort_values(["delta"] + DRIVER_KEYS, ascending=[False, True, True, True])
    return result.head(top).reset_index(drop=True)[columns]


def untagged_spend(df: pd.DataFrame, tag_keys: list[str]) -> dict:
    """Summarize spend that is missing any of ``tag_keys``.

    A row is untagged if at least one of the keys has no value (a key that is
    not in the data at all counts as missing everywhere). Returns a dict with
    ``cost`` (total untagged cost), ``share`` (fraction of total spend) and
    ``by_service`` (DataFrame with ``service``, ``cost`` and ``share`` of
    untagged cost, sorted descending).
    """
    if not tag_keys:
        raise ValueError("tag_keys must contain at least one tag key")

    mask = pd.Series(False, index=df.index)
    for key in tag_keys:
        column = _tag_column(key)
        if column in df.columns:
            mask |= df[column].isna()
        else:
            mask[:] = True

    untagged = df[mask]
    cost = total_spend(untagged)
    total = total_spend(df)
    by_service = untagged.groupby("service")["cost"].sum().reset_index()
    by_service["share"] = by_service["cost"] / cost if cost else np.nan
    by_service = by_service.sort_values(
        ["cost", "service"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)
    return {
        "cost": cost,
        "share": cost / total if total else float("nan"),
        "by_service": by_service,
    }


def validate_dimension(dimension: str) -> None:
    """Raise ``ValueError`` if ``dimension`` is not a supported breakdown."""
    if dimension in BASE_DIMENSIONS:
        return
    if dimension.startswith(TAG_DIMENSION_PREFIX) and dimension[len(TAG_DIMENSION_PREFIX):].strip():
        return
    raise ValueError(
        f"Unsupported dimension {dimension!r}; use one of "
        f"{', '.join(BASE_DIMENSIONS)} or tag:<key>"
    )


def _dimension_values(df: pd.DataFrame, dimension: str) -> pd.Series:
    validate_dimension(dimension)
    if dimension in BASE_DIMENSIONS:
        return df[dimension]
    column = _tag_column(dimension[len(TAG_DIMENSION_PREFIX):])
    if column not in df.columns:
        return pd.Series(UNTAGGED, index=df.index, name=column)
    return df[column].fillna(UNTAGGED)


def _tag_column(key: str) -> str:
    # The loader lowercases tag keys, so lookups are case-insensitive.
    return f"tag_{key.strip().lower()}"


def _with_changes(monthly: pd.Series) -> pd.DataFrame:
    previous = monthly.shift(1)
    change = monthly - previous
    pct = change / previous.where(previous != 0)
    return pd.DataFrame({"cost": monthly, "change": change, "pct_change": pct})
