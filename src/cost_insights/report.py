"""Markdown and self-contained HTML cost reports.

``build_report_data`` collects every number a report needs into a
``ReportData`` instance; ``render_markdown`` and ``render_html`` turn it into
text using the Jinja2 templates in ``cost_insights/templates``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
from jinja2 import Environment, PackageLoader, StrictUndefined

from cost_insights.analysis import monthly_trend, spend_by, top_drivers, total_spend, untagged_spend
from cost_insights.anomalies import Anomaly, detect_anomalies

TOP_N = 10


@dataclass
class ReportData:
    """Everything a cost report shows, as plain Python values."""

    period_start: date
    period_end: date
    total: float
    by_service: list[dict[str, Any]]
    by_account: list[dict[str, Any]]
    by_region: list[dict[str, Any]]
    by_tag: dict[str, list[dict[str, Any]]]
    trend: list[dict[str, Any]]
    top_drivers: list[dict[str, Any]]
    tag_keys: list[str]
    # ``cost``, ``share`` and ``by_service`` rows; None when there are no tag keys.
    untagged: dict[str, Any] | None
    anomalies: list[Anomaly] = field(default_factory=list)

    @property
    def mom_pct_change(self) -> float:
        """Change of the last month versus the one before, as a fraction (NaN if unknown)."""
        if len(self.trend) < 2:
            return float("nan")
        return self.trend[-1]["pct_change"]


def build_report_data(
    df: pd.DataFrame,
    tag_keys: list[str] | None = None,
    anomaly_kwargs: dict[str, Any] | None = None,
) -> ReportData:
    """Run the analyses on a normalized CUR DataFrame and collect the results.

    ``tag_keys`` are the tags shown as breakdowns and used for the untagged
    spend summary; when empty or None, every tag in the data is used.
    ``anomaly_kwargs`` is passed to ``detect_anomalies``.
    """
    if df.empty:
        raise ValueError("Cannot build a report from empty data")
    keys = list(tag_keys or sorted(c[len("tag_"):] for c in df.columns if c.startswith("tag_")))

    untagged = None
    if keys:
        summary = untagged_spend(df, keys)
        untagged = {
            "cost": summary["cost"],
            "share": summary["share"],
            "by_service": _records(summary["by_service"]),
        }

    drivers = top_drivers(df, top=TOP_N)
    return ReportData(
        period_start=df["usage_date"].min().date(),
        period_end=df["usage_date"].max().date(),
        total=total_spend(df),
        by_service=_records(spend_by(df, "service", top=TOP_N)),
        by_account=_records(spend_by(df, "account_id", top=TOP_N)),
        by_region=_records(spend_by(df, "region", top=TOP_N)),
        by_tag={key: _records(spend_by(df, f"tag:{key}", top=TOP_N)) for key in keys},
        trend=_records(monthly_trend(df)),
        top_drivers=_records(drivers) if not drivers.empty else [],
        tag_keys=keys,
        untagged=untagged,
        anomalies=detect_anomalies(df, **(anomaly_kwargs or {})),
    )


def render_markdown(data: ReportData) -> str:
    """Render the report as GitHub-flavored Markdown."""
    return _markdown_env.get_template("report.md.j2").render(data=data)


def render_html(data: ReportData) -> str:
    """Render the report as one self-contained HTML page (inline CSS, no scripts)."""
    return _html_env.get_template("report.html.j2").render(data=data)


def format_money(value: float, signed: bool = False) -> str:
    """Format as ``$1,234.56``; ``signed`` adds ``+`` to positive values."""
    if value is None or math.isnan(value):
        return "-"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.2f}"


def format_pct(value: float, signed: bool = False) -> str:
    """Format a fraction as a percentage with one decimal place."""
    if value is None or math.isnan(value):
        return "-"
    return f"{value:+.1%}" if signed else f"{value:.1%}"


def bar_width(share: float) -> str:
    """CSS width for a share bar, clamped to 0-100%."""
    if share is None or math.isnan(share):
        return "0%"
    return f"{min(max(share, 0.0), 1.0) * 100:.1f}%"


def _escape_markdown(value: Any) -> Any:
    # Keep data from breaking table cells or injecting HTML into rendered Markdown.
    if not isinstance(value, str):
        return value
    return (
        value.replace("\\", "\\\\")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for row in frame.to_dict(orient="records"):
        rows.append({k: (float(v) if isinstance(v, (int, float)) else str(v)) for k, v in row.items()})
    return rows


def _make_env(autoescape: bool) -> Environment:
    env = Environment(
        loader=PackageLoader("cost_insights", "templates"),
        autoescape=autoescape,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["money"] = format_money
    env.filters["pct"] = format_pct
    env.filters["bar_width"] = bar_width
    env.filters["md"] = _escape_markdown
    return env


_markdown_env = _make_env(autoescape=False)
_html_env = _make_env(autoescape=True)
