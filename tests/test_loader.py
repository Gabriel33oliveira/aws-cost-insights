import shutil
from pathlib import Path

import pandas as pd
import pytest

from cost_insights.loader import CANONICAL_COLUMNS, CurValidationError, load_cur

FIXTURE = Path(__file__).parent / "fixtures" / "mini_cur.csv"


def test_single_file_has_canonical_columns_and_dtypes():
    df = load_cur(str(FIXTURE))

    assert list(df.columns) == CANONICAL_COLUMNS + ["tag_team"]
    assert pd.api.types.is_datetime64_any_dtype(df["usage_date"])
    assert pd.api.types.is_float_dtype(df["cost"])
    assert all(isinstance(v, str) for v in df["account_id"])
    assert set(df["account_id"]) == {"012345678901", "000987654321"}
    assert set(df["service"]) == {"AmazonEC2", "AmazonS3", "AmazonRDS"}
    assert set(df["region"]) == {"us-east-1", "eu-west-1"}
    assert df["usage_date"].dt.to_period("M").nunique() == 2
    assert df.loc[0, "usage_date"] == pd.Timestamp("2026-01-03")
    assert df.loc[0, "cost"] == pytest.approx(12.5)


def test_credit_rows_excluded_by_default():
    df = load_cur(FIXTURE)
    assert len(df) == 11
    assert "Credit" not in set(df["line_item_type"])


def test_include_credits_keeps_credit_rows():
    df = load_cur(FIXTURE, include_credits=True)
    assert len(df) == 12
    assert (df["line_item_type"] == "Credit").sum() == 1
    assert df["cost"].min() == pytest.approx(-5.0)


def test_blank_tags_become_nan():
    df = load_cur(FIXTURE, include_credits=True)
    assert df["tag_team"].isna().sum() == 4
    assert "" not in set(df["tag_team"].dropna())


def test_list_of_files_is_concatenated(tmp_path):
    second = tmp_path / "second.csv"
    shutil.copy(FIXTURE, second)

    df = load_cur([FIXTURE, second])
    assert len(df) == 22


def test_directory_loads_every_csv(tmp_path):
    shutil.copy(FIXTURE, tmp_path / "a.csv")
    shutil.copy(FIXTURE, tmp_path / "b.csv")
    (tmp_path / "notes.txt").write_text("not a csv")

    df = load_cur(tmp_path)
    assert len(df) == 22


def test_region_falls_back_to_location(tmp_path):
    text = FIXTURE.read_text().replace("product/region", "product/location", 1)
    path = tmp_path / "location.csv"
    path.write_text(text)

    df = load_cur(path)
    assert set(df["region"]) == {"us-east-1", "eu-west-1"}


def test_tag_keys_are_lowercased_and_merged_across_files(tmp_path):
    path = tmp_path / "env.csv"
    path.write_text(
        "lineItem/UsageStartDate,lineItem/UsageAccountId,lineItem/ProductCode,"
        "product/region,lineItem/UnblendedCost,lineItem/LineItemType,"
        "lineItem/UsageType,resourceTags/user:Environment\n"
        "2026-03-01T00:00:00Z,012345678901,AmazonEC2,us-east-1,3.0,Usage,BoxUsage,Prod\n"
    )

    df = load_cur([FIXTURE, path])
    assert "tag_environment" in df.columns
    assert "tag_team" in df.columns
    assert df["tag_environment"].notna().sum() == 1


def test_missing_required_column_raises(tmp_path):
    text = FIXTURE.read_text().replace("lineItem/UnblendedCost", "lineItem/BlendedCost", 1)
    path = tmp_path / "bad.csv"
    path.write_text(text)

    with pytest.raises(CurValidationError, match="lineItem/UnblendedCost"):
        load_cur(path)


def test_empty_directory_raises(tmp_path):
    with pytest.raises(CurValidationError):
        load_cur(tmp_path)


def test_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_cur(tmp_path / "nope.csv")
