from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from cost_insights.cli import app
from cost_insights.loader import load_cur
from cost_insights.synthetic import SERVICES, generate_cur, write_cur_csv

SAMPLE = Path(__file__).parent.parent / "data" / "samples" / "sample_cur.csv"
START = date(2026, 1, 1)


@pytest.fixture(scope="module")
def generated():
    return generate_cur(START, months=2, accounts=3, seed=7)


def test_same_seed_is_identical(generated):
    again = generate_cur(START, months=2, accounts=3, seed=7)
    pd.testing.assert_frame_equal(generated, again)
    assert generated.attrs == again.attrs


def test_different_seed_differs(generated):
    other = generate_cur(START, months=2, accounts=3, seed=8)
    assert not generated.equals(other)


def test_round_trip_through_loader(generated, tmp_path):
    path = write_cur_csv(generated, tmp_path / "cur.csv")
    df = load_cur(path)

    assert df["account_id"].nunique() == 3
    assert all(len(a) == 12 and a.isdigit() for a in df["account_id"].unique())
    assert set(df["service"]) == set(SERVICES)
    assert set(df["region"]) == {"us-east-1", "eu-west-1", "sa-east-1"}
    months = df["usage_date"].dt.to_period("M").unique()
    assert [str(m) for m in months] == ["2026-01", "2026-02"]
    assert {"tag_team", "tag_env"} <= set(df.columns)
    assert (df["cost"] > 0).all()


def test_credit_rows_present(generated, tmp_path):
    df = load_cur(write_cur_csv(generated, tmp_path / "cur.csv"), include_credits=True)
    credits = df[df["line_item_type"] == "Credit"]
    assert len(credits) > 0
    assert (credits["cost"] < 0).all()


def test_untagged_fraction_in_range(generated):
    usage = generated[generated["lineItem/LineItemType"] == "Usage"]
    untagged = (usage["resourceTags/user:team"] == "").mean()
    assert 0.05 <= untagged <= 0.30


def test_anomalies_recorded(generated):
    dates = generated.attrs["anomalies"]
    assert len(dates) == 2
    assert len(set(dates)) == 2
    all_dates = set(pd.to_datetime(generated["lineItem/UsageStartDate"]).dt.date)
    assert set(dates) <= all_dates

    # The EC2 spike day should cost far more than the account's usual EC2 day.
    ec2 = next(d for d in generated.attrs["anomaly_details"] if d["service"] == "AmazonEC2")
    rows = generated[
        (generated["lineItem/ProductCode"] == "AmazonEC2")
        & (generated["lineItem/UsageAccountId"] == ec2["account_id"])
        & (generated["lineItem/LineItemType"] == "Usage")
    ]
    daily = rows.groupby("lineItem/UsageStartDate")["lineItem/UnblendedCost"].sum()
    spike_key = ec2["date"].strftime("%Y-%m-%dT00:00:00Z")
    assert daily[spike_key] > 2.5 * daily.drop(spike_key).median()


def test_no_anomalies_option(generated):
    clean = generate_cur(START, months=2, accounts=3, seed=7, anomalies=False)
    assert clean.attrs["anomalies"] == []
    # Only the spiked rows differ from the anomalous version.
    changed = clean["lineItem/UnblendedCost"] != generated["lineItem/UnblendedCost"]
    changed_dates = set(pd.to_datetime(clean.loc[changed, "lineItem/UsageStartDate"]).dt.date)
    assert 0 < changed.sum() and changed_dates <= set(generated.attrs["anomalies"])


def test_cli_generate(tmp_path):
    out = tmp_path / "x.csv"
    result = CliRunner().invoke(
        app, ["generate", "--out", str(out), "--months", "1", "--accounts", "1", "--seed", "1"]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    df = load_cur(out)
    assert df["account_id"].nunique() == 1


def test_cli_generate_no_anomalies(tmp_path):
    out = tmp_path / "x.csv"
    result = CliRunner().invoke(
        app, ["generate", "--out", str(out), "--months", "1", "--seed", "1", "--no-anomalies"]
    )
    assert result.exit_code == 0, result.output
    assert "Injected anomaly" not in result.output


def test_committed_sample_matches_generator():
    assert SAMPLE.exists()
    assert SAMPLE.stat().st_size < 1_000_000
    df = load_cur(SAMPLE)
    assert df["account_id"].nunique() == 2
    assert df["usage_date"].dt.to_period("M").nunique() == 2
