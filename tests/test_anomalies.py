from dataclasses import FrozenInstanceError
from datetime import date

import pandas as pd
import pytest
from typer.testing import CliRunner

from cost_insights.anomalies import Anomaly, detect_anomalies
from cost_insights.cli import app
from cost_insights.loader import load_cur
from cost_insights.synthetic import generate_cur, write_cur_csv

START = date(2026, 1, 1)


def daily_frame(costs: list[float], service: str = "AmazonEC2", **columns) -> pd.DataFrame:
    """Build a normalized frame with one row per day for a single key."""
    return pd.DataFrame(
        {
            "usage_date": pd.date_range(START, periods=len(costs), freq="D"),
            "account_id": columns.get("account_id", "111111111111"),
            "service": service,
            "region": columns.get("region", "us-east-1"),
            "cost": [float(c) for c in costs],
        }
    )


def load_generated(tmp_path, anomalies: bool) -> tuple[pd.DataFrame, list[date]]:
    raw = generate_cur(START, months=3, accounts=3, seed=7, anomalies=anomalies)
    path = write_cur_csv(raw, tmp_path / "cur.csv")
    return load_cur(path), raw.attrs["anomalies"]


def test_single_spike_is_flagged_with_baseline_and_pct_change():
    costs = [100.0] * 20
    costs[15] = 500.0
    result = detect_anomalies(daily_frame(costs))

    assert len(result) == 1
    anomaly = result[0]
    assert anomaly.date == date(2026, 1, 16)
    assert anomaly.dimension == "service"
    assert anomaly.key == "AmazonEC2"
    assert anomaly.cost == pytest.approx(500.0)
    assert anomaly.baseline == pytest.approx(100.0)
    assert anomaly.pct_change == pytest.approx(4.0)


def test_spike_on_noisy_series_uses_robust_z_score():
    costs = [100.0, 112.0, 88.0, 106.0, 94.0] * 4
    costs[17] = 300.0
    result = detect_anomalies(daily_frame(costs))

    assert [a.date for a in result] == [date(2026, 1, 18)]
    window = pd.Series(costs[3:17])
    median = window.median()
    mad = (window - median).abs().median()
    assert mad > 0.05 * median  # above the MAD floor
    assert result[0].baseline == pytest.approx(median)
    assert result[0].score == pytest.approx((300.0 - median) / (1.4826 * mad))


def test_early_spike_uses_robust_z_score_on_partial_history():
    costs = [100.0, 112.0, 88.0, 106.0, 94.0] * 4
    costs[8] = 150.0  # only 8 prior days, fewer than the window
    result = detect_anomalies(daily_frame(costs))

    assert [a.date for a in result] == [date(2026, 1, 9)]
    window = pd.Series(costs[:8])
    median = window.median()
    mad = (window - median).abs().median()
    assert result[0].score == pytest.approx((150.0 - median) / (1.4826 * mad))


def test_small_jitter_on_smooth_series_is_not_flagged():
    costs = [100.0, 101.0, 99.0, 100.5, 99.5] * 4
    costs[17] = 112.0  # +12%: many MADs away, but within normal day-to-day noise
    assert detect_anomalies(daily_frame(costs)) == []


def test_flat_series_has_no_anomalies():
    assert detect_anomalies(daily_frame([100.0] * 30)) == []


def test_days_without_enough_history_are_never_flagged():
    costs = [100.0] * 20
    costs[6] = 1000.0  # only 6 prior days
    assert detect_anomalies(daily_frame(costs)) == []

    costs[7] = 1000.0  # 7 prior days, flagged
    assert [a.date for a in detect_anomalies(daily_frame(costs))] == [date(2026, 1, 8)]


def test_spikes_below_min_cost_are_ignored():
    costs = [1.0] * 20
    costs[15] = 5.0
    assert detect_anomalies(daily_frame(costs)) == []
    assert len(detect_anomalies(daily_frame(costs), min_cost=1.0)) == 1


def test_results_sorted_by_score_and_grouped_by_key():
    small = [100.0] * 20
    small[10] = 300.0
    big = [100.0] * 20
    big[12] = 900.0
    df = pd.concat([daily_frame(small, "AmazonS3"), daily_frame(big, "AmazonEC2")])

    result = detect_anomalies(df)
    assert [(a.key, a.date) for a in result] == [
        ("AmazonEC2", date(2026, 1, 13)),
        ("AmazonS3", date(2026, 1, 11)),
    ]
    assert result[0].score > result[1].score


def test_overall_total_and_other_dimensions():
    costs = [50.0] * 20
    costs[14] = 400.0
    df = pd.concat([daily_frame(costs, "AmazonEC2"), daily_frame([50.0] * 20, "AmazonS3")])

    total = detect_anomalies(df, by=None)
    assert len(total) == 1
    assert total[0].dimension is None
    assert total[0].cost == pytest.approx(450.0)
    assert total[0].baseline == pytest.approx(100.0)

    by_region = detect_anomalies(df, by="region")
    assert [a.key for a in by_region] == ["us-east-1"]


def test_missing_days_count_as_zero():
    df = daily_frame([100.0] * 20)
    df = df[df["usage_date"] != pd.Timestamp("2026-01-12")]
    assert detect_anomalies(df) == []


def test_invalid_dimension_raises():
    with pytest.raises(ValueError, match="Unsupported dimension"):
        detect_anomalies(daily_frame([1.0] * 10), by="tag:team")


def test_anomaly_is_frozen():
    anomaly = Anomaly(date(2026, 1, 1), "service", "AmazonEC2", 1.0, 1.0, 0.0, 0.0)
    with pytest.raises(FrozenInstanceError):
        anomaly.cost = 2.0


def test_detects_every_injected_synthetic_anomaly(tmp_path):
    df, expected = load_generated(tmp_path, anomalies=True)
    assert expected
    detected = {a.date for a in detect_anomalies(df, by="service")}
    assert set(expected) <= detected


def test_at_most_one_false_positive_without_injected_anomalies(tmp_path):
    df, expected = load_generated(tmp_path, anomalies=False)
    assert expected == []
    assert len(detect_anomalies(df, by="service")) <= 1


def test_cli_anomalies_on_generated_csv(tmp_path):
    raw = generate_cur(START, months=3, accounts=3, seed=7, anomalies=True)
    path = write_cur_csv(raw, tmp_path / "cur.csv")
    runner = CliRunner()

    result = runner.invoke(app, ["anomalies", str(path), "--by", "service"])
    assert result.exit_code == 0, result.output
    assert raw.attrs["anomalies"][0].isoformat() in result.output

    quiet = runner.invoke(app, ["anomalies", str(path), "--min-cost", "1000000"])
    assert quiet.exit_code == 0, quiet.output
    assert "No anomalies detected" in quiet.output


def test_cli_anomalies_rejects_unknown_dimension(tmp_path):
    raw = generate_cur(START, months=1, accounts=1, seed=7)
    path = write_cur_csv(raw, tmp_path / "cur.csv")
    result = CliRunner().invoke(app, ["anomalies", str(path), "--by", "tag:team"])
    assert result.exit_code != 0
