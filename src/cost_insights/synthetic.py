"""Deterministic synthetic CUR data for demos and tests.

``generate_cur`` builds daily usage rows with the raw CUR column names that
``cost_insights.loader`` accepts, so its output can be written to CSV and
loaded back like a real export. The same seed always gives the same data.
"""

from __future__ import annotations

from datetime import date
from os import PathLike
from pathlib import Path

import numpy as np
import pandas as pd

# Service -> (min, max) baseline daily cost in USD for one account in us-east-1.
SERVICES: dict[str, tuple[float, float]] = {
    "AmazonEC2": (80.0, 160.0),
    "AmazonS3": (10.0, 30.0),
    "AmazonRDS": (40.0, 90.0),
    "AWSLambda": (2.0, 8.0),
    "AmazonCloudFront": (5.0, 20.0),
    "AmazonDynamoDB": (4.0, 15.0),
}

USAGE_TYPES: dict[str, str] = {
    "AmazonEC2": "BoxUsage:m5.large",
    "AmazonS3": "TimedStorage-ByteHrs",
    "AmazonRDS": "InstanceUsage:db.m5.large",
    "AWSLambda": "Lambda-GB-Second",
    "AmazonCloudFront": "DataTransfer-Out-Bytes",
    "AmazonDynamoDB": "ReadCapacityUnit-Hrs",
}

# Region -> (usage type prefix, cost multiplier).
REGIONS: dict[str, tuple[str, float]] = {
    "us-east-1": ("USE1", 1.0),
    "eu-west-1": ("EU", 0.6),
    "sa-east-1": ("SAE1", 0.3),
}

# Weekend cost multiplier per service; traffic-driven services drop more.
WEEKEND_FACTOR: dict[str, float] = {
    "AmazonEC2": 0.85,
    "AmazonS3": 1.0,
    "AmazonRDS": 0.9,
    "AWSLambda": 0.65,
    "AmazonCloudFront": 0.7,
    "AmazonDynamoDB": 0.8,
}

TEAMS = ("platform", "data", "web", "ml")
ENVS = ("prod", "staging", "dev")

MONTHLY_GROWTH = 0.03
NOISE_SIGMA = 0.08
UNTAGGED_FRACTION = 0.15
CREDITS_PER_ACCOUNT_MONTH = 2

# (service, cost multiplier) for the injected spikes.
ANOMALY_SPECS: tuple[tuple[str, float], ...] = (
    ("AmazonEC2", 4.0),
    ("AWSLambda", 6.0),
)

TEAM_TAG = "resourceTags/user:team"
ENV_TAG = "resourceTags/user:env"

COLUMNS = [
    "identity/LineItemId",
    "lineItem/UsageStartDate",
    "lineItem/UsageEndDate",
    "lineItem/UsageAccountId",
    "lineItem/LineItemType",
    "lineItem/ProductCode",
    "lineItem/UsageType",
    "lineItem/UsageAmount",
    "lineItem/UnblendedCost",
    "product/region",
    TEAM_TAG,
    ENV_TAG,
]


def generate_cur(
    start: date,
    months: int,
    accounts: int,
    seed: int,
    anomalies: bool = True,
) -> pd.DataFrame:
    """Generate synthetic daily CUR rows.

    Covers ``months`` calendar months starting at ``start`` for ``accounts``
    linked accounts. When ``anomalies`` is True, two cost spikes are injected
    and their dates are listed in ``df.attrs["anomalies"]``; details (account,
    service, factor) are in ``df.attrs["anomaly_details"]``. Both lists are
    empty when ``anomalies`` is False. Apart from the spikes, the data is the
    same either way for a given seed.
    """
    if months < 1:
        raise ValueError("months must be at least 1")
    if accounts < 1:
        raise ValueError("accounts must be at least 1")

    rng = np.random.default_rng(seed)
    days = pd.date_range(
        pd.Timestamp(start), pd.Timestamp(start) + pd.DateOffset(months=months), inclusive="left"
    )
    account_ids = _account_ids(rng, accounts)
    services = list(SERVICES)
    regions = list(REGIONS)

    # Per (account, service, region) baseline cost and per (account, service) tags.
    low = np.array([SERVICES[s][0] for s in services])
    high = np.array([SERVICES[s][1] for s in services])
    baseline = rng.uniform(low, high, size=(accounts, len(services)))
    region_mult = np.array([REGIONS[r][1] for r in regions])
    region_share = rng.uniform(0.7, 1.3, size=(accounts, len(services), len(regions)))
    base = baseline[:, :, None] * region_mult[None, None, :] * region_share
    teams = rng.choice(TEAMS, size=(accounts, len(services)))
    envs = rng.choice(ENVS, size=accounts)

    # Full grid: day x account x service x region.
    n_days, n_acc, n_svc, n_reg = len(days), accounts, len(services), len(regions)
    shape = (n_days, n_acc, n_svc, n_reg)
    d_idx, a_idx, s_idx, r_idx = (idx.ravel() for idx in np.indices(shape))

    elapsed_months = (days - days[0]).days.to_numpy() / 30.0
    growth = (1 + MONTHLY_GROWTH) ** elapsed_months
    weekend = days.dayofweek.to_numpy() >= 5
    weekend_mult = np.where(
        weekend[:, None], np.array([WEEKEND_FACTOR[s] for s in services])[None, :], 1.0
    )
    noise = rng.lognormal(0.0, NOISE_SIGMA, size=d_idx.size)
    cost = base[a_idx, s_idx, r_idx] * growth[d_idx] * weekend_mult[d_idx, s_idx] * noise

    untagged = rng.random(d_idx.size) < UNTAGGED_FRACTION

    # Spike days are always drawn so anomalies=False only removes the spikes.
    spike_days = _spike_days(rng, n_days)
    spike_accounts = [0, n_acc - 1]
    details: list[dict] = []
    if anomalies:
        for (service, factor), day_i, acc_i in zip(ANOMALY_SPECS, spike_days, spike_accounts):
            mask = (d_idx == day_i) & (a_idx == acc_i) & (s_idx == services.index(service))
            cost[mask] *= factor
            details.append(
                {
                    "date": days[day_i].date(),
                    "account_id": account_ids[acc_i],
                    "service": service,
                    "factor": factor,
                }
            )

    service_arr = np.array(services)[s_idx]
    region_arr = np.array(regions)[r_idx]
    prefixes = np.array([REGIONS[r][0] for r in regions])[r_idx]
    usage_types = np.char.add(
        np.char.add(prefixes, "-"), np.array([USAGE_TYPES[s] for s in services])[s_idx]
    )
    # Usage amount is a rough proxy: cost divided by a per-service unit price.
    unit_price = rng.uniform(0.05, 0.2, size=n_svc)[s_idx]

    usage = pd.DataFrame(
        {
            "lineItem/UsageStartDate": days[d_idx],
            "lineItem/UsageAccountId": np.array(account_ids)[a_idx],
            "lineItem/LineItemType": "Usage",
            "lineItem/ProductCode": service_arr,
            "lineItem/UsageType": usage_types,
            "lineItem/UsageAmount": np.round(cost / unit_price, 4),
            "lineItem/UnblendedCost": np.round(cost, 4),
            "product/region": region_arr,
            TEAM_TAG: np.where(untagged, "", teams[a_idx, s_idx]),
            ENV_TAG: np.where(untagged, "", envs[a_idx]),
        }
    )

    df = pd.concat([usage, _credit_rows(rng, days, account_ids)], ignore_index=True)
    df = df.sort_values(
        ["lineItem/UsageStartDate", "lineItem/UsageAccountId", "lineItem/LineItemType"],
        kind="stable",
    ).reset_index(drop=True)

    df["identity/LineItemId"] = [f"li-{i:07d}" for i in range(len(df))]
    starts = df["lineItem/UsageStartDate"].to_numpy(dtype="datetime64[s]")
    df["lineItem/UsageEndDate"] = _iso(starts + np.timedelta64(1, "D"))
    df["lineItem/UsageStartDate"] = _iso(starts)
    df = df[COLUMNS]

    df.attrs["anomalies"] = [d["date"] for d in details]
    df.attrs["anomaly_details"] = details
    return df


def write_cur_csv(df: pd.DataFrame, path: str | PathLike) -> Path:
    """Write a generated CUR DataFrame to ``path`` as CSV and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def _account_ids(rng: np.random.Generator, count: int) -> list[str]:
    ids: list[str] = []
    while len(ids) < count:
        candidate = f"{int(rng.integers(10**11, 10**12)):012d}"
        if candidate not in ids:
            ids.append(candidate)
    return ids


def _spike_days(rng: np.random.Generator, n_days: int) -> list[int]:
    # Keep spikes off the first week so a detector has history before them.
    # A month always has at least 28 days, so there are enough candidates.
    candidates = np.arange(7, n_days - 2)
    return [int(d) for d in rng.choice(candidates, size=len(ANOMALY_SPECS), replace=False)]


def _credit_rows(
    rng: np.random.Generator, days: pd.DatetimeIndex, account_ids: list[str]
) -> pd.DataFrame:
    rows = []
    for period in days.to_period("M").unique():
        month_days = days[days.to_period("M") == period]
        for account_id in account_ids:
            for _ in range(CREDITS_PER_ACCOUNT_MONTH):
                day = month_days[int(rng.integers(len(month_days)))]
                rows.append(
                    {
                        "lineItem/UsageStartDate": day,
                        "lineItem/UsageAccountId": account_id,
                        "lineItem/LineItemType": "Credit",
                        "lineItem/ProductCode": "AmazonEC2",
                        "lineItem/UsageType": "USE1-BoxUsage:m5.large",
                        "lineItem/UsageAmount": 0.0,
                        "lineItem/UnblendedCost": -round(float(rng.uniform(5.0, 50.0)), 4),
                        "product/region": "us-east-1",
                        TEAM_TAG: "",
                        ENV_TAG: "",
                    }
                )
    return pd.DataFrame(rows)


def _iso(values: np.ndarray) -> np.ndarray:
    return np.char.add(np.datetime_as_string(values, unit="s"), "Z")
