"""Analysis tests on tests/fixtures/mini_cur.csv.

With the Credit row excluded the fixture has 11 usage rows:
January totals 61.30 and February totals 64.05, so total spend is 125.35.
"""

import math
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from cost_insights.analysis import (
    UNTAGGED,
    monthly_trend,
    spend_by,
    top_drivers,
    total_spend,
    untagged_spend,
)
from cost_insights.cli import app
from cost_insights.loader import load_cur

FIXTURE = Path(__file__).parent / "fixtures" / "mini_cur.csv"
EC2_USE1 = "012345678901"
EU_ACCOUNT = "000987654321"


@pytest.fixture(scope="module")
def df():
    return load_cur(FIXTURE)


def as_dict(frame):
    return dict(zip(frame["key"], frame["cost"]))


def test_total_spend(df):
    assert total_spend(df) == pytest.approx(125.35)


def test_total_spend_with_credits():
    assert total_spend(load_cur(FIXTURE, include_credits=True)) == pytest.approx(120.35)


def test_spend_by_service(df):
    result = spend_by(df, "service")
    assert list(result.columns) == ["key", "cost", "share"]
    assert list(result["key"]) == ["AmazonRDS", "AmazonEC2", "AmazonS3"]
    assert result["cost"].tolist() == pytest.approx([79.10, 42.80, 3.45])
    assert result["share"].sum() == pytest.approx(1.0)
    assert result.loc[0, "share"] == pytest.approx(79.10 / 125.35)


def test_spend_by_region(df):
    result = spend_by(df, "region")
    assert as_dict(result) == pytest.approx({"us-east-1": 66.05, "eu-west-1": 59.30})
    assert result["share"].sum() == pytest.approx(1.0)


def test_spend_by_account(df):
    result = spend_by(df, "account_id")
    assert as_dict(result) == pytest.approx({EC2_USE1: 66.05, EU_ACCOUNT: 59.30})


def test_spend_by_tag_groups_missing_as_untagged(df):
    result = spend_by(df, "tag:team")
    assert list(result["key"]) == ["data", "platform", UNTAGGED]
    assert result["cost"].tolist() == pytest.approx([61.60, 35.60, 28.15])
    assert result["share"].sum() == pytest.approx(1.0)


def test_spend_by_tag_key_is_case_insensitive(df):
    assert as_dict(spend_by(df, "tag:Team")) == as_dict(spend_by(df, "tag:team"))


def test_spend_by_unknown_tag_is_all_untagged(df):
    result = spend_by(df, "tag:owner")
    assert list(result["key"]) == [UNTAGGED]
    assert result.loc[0, "share"] == pytest.approx(1.0)


def test_spend_by_top_limits_rows_but_keeps_shares(df):
    result = spend_by(df, "service", top=2)
    assert list(result["key"]) == ["AmazonRDS", "AmazonEC2"]
    assert result["share"].sum() == pytest.approx((79.10 + 42.80) / 125.35)


@pytest.mark.parametrize("dimension", ["usage_type", "tag:", "Service", ""])
def test_unsupported_dimension_raises(df, dimension):
    with pytest.raises(ValueError):
        spend_by(df, dimension)


def test_monthly_trend_raises_on_unsupported_dimension(df):
    with pytest.raises(ValueError):
        monthly_trend(df, "nope")


def test_monthly_trend_totals(df):
    result = monthly_trend(df)
    assert list(result.columns) == ["month", "cost", "change", "pct_change"]
    assert list(result["month"]) == ["2026-01", "2026-02"]
    assert result["cost"].tolist() == pytest.approx([61.30, 64.05])
    assert math.isnan(result.loc[0, "change"])
    assert math.isnan(result.loc[0, "pct_change"])
    assert result.loc[1, "change"] == pytest.approx(2.75)
    assert result.loc[1, "pct_change"] == pytest.approx(2.75 / 61.30)


def test_monthly_trend_by_service(df):
    result = monthly_trend(df, "service")
    assert list(result.columns) == ["key", "month", "cost", "change", "pct_change"]
    rows = result.set_index(["key", "month"])

    assert rows.loc[("AmazonEC2", "2026-01"), "cost"] == pytest.approx(20.50)
    assert rows.loc[("AmazonEC2", "2026-02"), "pct_change"] == pytest.approx(1.80 / 20.50)
    assert rows.loc[("AmazonS3", "2026-02"), "change"] == pytest.approx(1.40 - 2.05)
    assert rows.loc[("AmazonRDS", "2026-02"), "pct_change"] == pytest.approx(1.60 / 38.75)
    assert result.loc[result["month"] == "2026-01", "pct_change"].isna().all()


def test_monthly_trend_by_dimension_fills_missing_months_with_zero():
    frame = pd.DataFrame(
        {
            "usage_date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-02-01"]),
            "service": ["A", "A", "B"],
            "cost": [10.0, 5.0, 4.0],
        }
    )
    rows = monthly_trend(frame, "service").set_index(["key", "month"])
    assert rows.loc[("B", "2026-01"), "cost"] == 0.0
    assert rows.loc[("B", "2026-02"), "change"] == pytest.approx(4.0)
    # Growth from zero has no meaningful percentage.
    assert math.isnan(rows.loc[("B", "2026-02"), "pct_change"])
    assert rows.loc[("A", "2026-02"), "pct_change"] == pytest.approx(-0.5)


def test_top_drivers_order_and_deltas(df):
    result = top_drivers(df, top=10)
    assert list(result.columns) == [
        "service", "account_id", "region", "previous_cost", "current_cost", "delta"
    ]
    combos = list(zip(result["service"], result["account_id"], result["region"]))
    assert combos == [
        ("AmazonRDS", EU_ACCOUNT, "eu-west-1"),
        ("AmazonEC2", EU_ACCOUNT, "eu-west-1"),
        ("AmazonEC2", EC2_USE1, "us-east-1"),
        ("AmazonRDS", EC2_USE1, "us-east-1"),
        ("AmazonS3", EC2_USE1, "us-east-1"),
        ("AmazonS3", EU_ACCOUNT, "eu-west-1"),
    ]
    assert result["delta"].tolist() == pytest.approx([1.30, 1.20, 0.60, 0.30, 0.15, -0.80])
    assert result.loc[0, "previous_cost"] == pytest.approx(20.00)
    assert result.loc[0, "current_cost"] == pytest.approx(21.30)
    # S3 in eu-west-1 has no February spend, so it counts as 0 there.
    assert result.loc[5, "current_cost"] == 0.0


def test_top_drivers_default_top(df):
    assert len(top_drivers(df)) == 5


def test_top_drivers_single_month_is_empty(df):
    january = df[df["usage_date"] < "2026-02-01"]
    assert top_drivers(january).empty


def test_untagged_spend(df):
    result = untagged_spend(df, ["team"])
    assert result["cost"] == pytest.approx(28.15)
    assert result["share"] == pytest.approx(28.15 / 125.35)
    by_service = result["by_service"]
    assert list(by_service["service"]) == ["AmazonRDS", "AmazonEC2", "AmazonS3"]
    assert by_service["cost"].tolist() == pytest.approx([18.75, 8.00, 1.40])
    assert by_service["share"].sum() == pytest.approx(1.0)


def test_untagged_spend_any_missing_key_counts(df):
    # "owner" is not in the data, so every row is missing at least one key.
    result = untagged_spend(df, ["team", "owner"])
    assert result["cost"] == pytest.approx(125.35)
    assert result["share"] == pytest.approx(1.0)


def test_untagged_spend_requires_keys(df):
    with pytest.raises(ValueError):
        untagged_spend(df, [])


def test_functions_do_not_modify_input(df):
    before = df.copy()
    spend_by(df, "tag:team")
    monthly_trend(df, "region")
    top_drivers(df)
    untagged_spend(df, ["team"])
    pd.testing.assert_frame_equal(df, before)


def test_cli_summary():
    result = CliRunner().invoke(
        app, ["summary", str(FIXTURE), "--by", "service", "--by", "region"]
    )
    assert result.exit_code == 0, result.output
    assert "AmazonRDS" in result.output
    assert "eu-west-1" in result.output
    assert "$125.35" in result.output
    assert "Month-over-month" in result.output
    assert "$28.15" in result.output


def test_cli_summary_rejects_bad_dimension():
    result = CliRunner().invoke(app, ["summary", str(FIXTURE), "--by", "color"])
    assert result.exit_code != 0
