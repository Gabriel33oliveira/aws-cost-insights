"""Load AWS Cost and Usage Report (CUR) style CSV files into a normalized DataFrame."""

from __future__ import annotations

from collections.abc import Iterable
from os import PathLike
from pathlib import Path

import pandas as pd

TAG_PREFIX = "resourceTags/user:"

# Canonical name -> candidate CUR column names, in order of preference.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "usage_date": ("lineItem/UsageStartDate",),
    "account_id": ("lineItem/UsageAccountId",),
    "service": ("lineItem/ProductCode",),
    "region": ("product/region", "product/location"),
    "cost": ("lineItem/UnblendedCost",),
    "line_item_type": ("lineItem/LineItemType",),
    "usage_type": ("lineItem/UsageType",),
}

CANONICAL_COLUMNS = list(REQUIRED_COLUMNS)

EXCLUDED_LINE_ITEM_TYPES = ("Tax", "Credit")

PathInput = str | PathLike | Iterable[str | PathLike]


class CurValidationError(ValueError):
    """Raised when a CUR file is missing required columns or cannot be parsed."""


def load_cur(path_or_paths: PathInput, include_credits: bool = False) -> pd.DataFrame:
    """Load one or more CUR CSV files into a single normalized DataFrame.

    ``path_or_paths`` may be a single CSV file, a directory (every ``*.csv``
    inside it is loaded) or a list of file paths. Rows whose line item type is
    ``Tax`` or ``Credit`` are dropped unless ``include_credits`` is True.

    The result has the columns in ``CANONICAL_COLUMNS`` followed by one
    ``tag_<key>`` column per ``resourceTags/user:<key>`` column. ``usage_date``
    is a timezone-naive datetime normalized to midnight UTC, ``account_id`` is a
    string and ``cost`` is a float.
    """
    files = _resolve_files(path_or_paths)
    frames = [_load_file(path) for path in files]
    df = pd.concat(frames, ignore_index=True, sort=False)

    tag_columns = sorted(c for c in df.columns if c.startswith("tag_"))
    df = df[CANONICAL_COLUMNS + tag_columns]

    if not include_credits:
        df = df[~df["line_item_type"].isin(EXCLUDED_LINE_ITEM_TYPES)]

    return df.reset_index(drop=True)


def _resolve_files(path_or_paths: PathInput) -> list[Path]:
    if isinstance(path_or_paths, (str, PathLike)):
        candidates = [Path(path_or_paths)]
    else:
        candidates = [Path(p) for p in path_or_paths]

    files: list[Path] = []
    for path in candidates:
        if path.is_dir():
            files.extend(sorted(path.glob("*.csv")))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"CUR path not found: {path}")

    if not files:
        raise CurValidationError(f"No CSV files found in: {', '.join(map(str, candidates))}")
    return files


def _load_file(path: Path) -> pd.DataFrame:
    # Read everything as text so account IDs keep their leading zeros.
    raw = pd.read_csv(path, dtype=str)

    missing = [
        " or ".join(names)
        for names in REQUIRED_COLUMNS.values()
        if not any(name in raw.columns for name in names)
    ]
    if missing:
        raise CurValidationError(
            f"{path}: missing required CUR column(s): {', '.join(missing)}"
        )

    df = pd.DataFrame(index=raw.index)
    for canonical, names in REQUIRED_COLUMNS.items():
        source = next(name for name in names if name in raw.columns)
        df[canonical] = raw[source].str.strip()

    try:
        dates = pd.to_datetime(df["usage_date"], utc=True, format="ISO8601")
        df["usage_date"] = dates.dt.tz_localize(None).dt.normalize()
    except (ValueError, TypeError) as exc:
        raise CurValidationError(f"{path}: invalid value in lineItem/UsageStartDate: {exc}") from exc
    try:
        df["cost"] = pd.to_numeric(df["cost"]).astype(float)
    except (ValueError, TypeError) as exc:
        raise CurValidationError(f"{path}: invalid value in lineItem/UnblendedCost: {exc}") from exc

    for column in raw.columns:
        if column.startswith(TAG_PREFIX):
            key = column[len(TAG_PREFIX):].strip().lower()
            values = raw[column].str.strip()
            # Blank or whitespace-only tag values become NaN.
            df[f"tag_{key}"] = values.mask(values == "")

    return df
