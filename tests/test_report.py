import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cost_insights.cli import app
from cost_insights.loader import load_cur
from cost_insights.report import (
    build_report_data,
    format_money,
    format_pct,
    render_html,
    render_markdown,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mini_cur.csv"
SECTIONS = [
    "Summary",
    "Spend by Service",
    "Month-over-Month",
    "Top Cost Drivers",
    "Untagged Spend",
    "Anomalies",
]

runner = CliRunner()


@pytest.fixture
def data():
    return build_report_data(load_cur(FIXTURE), ["team"], {})


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []
        self.headings = []
        self._in_h2 = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)
        self._in_h2 = tag == "h2"

    def handle_endtag(self, tag):
        if tag == "h2":
            self._in_h2 = False

    def handle_data(self, text):
        if self._in_h2:
            self.headings.append(text.strip())


def _parse(html):
    parser = _Collector()
    parser.feed(html)
    parser.close()
    return parser


def test_build_report_data(data):
    assert data.period_start == date(2026, 1, 3)
    assert data.period_end == date(2026, 2, 20)
    assert data.total == pytest.approx(125.35)
    assert [row["key"] for row in data.by_service] == ["AmazonRDS", "AmazonEC2", "AmazonS3"]
    assert set(data.by_tag) == {"team"}
    assert [row["month"] for row in data.trend] == ["2026-01", "2026-02"]
    assert data.mom_pct_change == pytest.approx(2.75 / 61.30)
    assert data.untagged["cost"] == pytest.approx(28.15)
    assert data.top_drivers
    # The fixture is sparse, so some days jump from a zero baseline.
    assert len(data.anomalies) == 5


def test_build_report_data_defaults_to_all_tags():
    data = build_report_data(load_cur(FIXTURE), None, None)
    assert data.tag_keys == ["team"]


def test_formatting_helpers():
    assert format_money(1234.5) == "$1,234.50"
    assert format_money(-5) == "-$5.00"
    assert format_money(2.75, signed=True) == "+$2.75"
    assert format_money(float("nan")) == "-"
    assert format_pct(0.04486) == "4.5%"
    assert format_pct(0.04486, signed=True) == "+4.5%"


def test_markdown_sections_and_totals(data):
    text = render_markdown(data)
    for section in SECTIONS:
        assert f"## {section}\n" in text
    assert "| Total spend | $125.35 |" in text
    assert "| Month-over-month change | +4.5% |" in text
    assert "| Untagged share | 22.5% |" in text
    assert "| AmazonRDS | $79.10 | 63.1% |" in text
    assert "| 2026-02 | $64.05 | +$2.75 | +4.5% |" in text
    assert "| Anomalies | 5 |" in text
    assert "| 2026-02-14 | AmazonRDS | $21.30 | $0.00 | - | inf |" in text


def test_markdown_without_anomalies(data):
    data.anomalies = []
    assert "No anomalies detected." in render_markdown(data)


def test_markdown_escapes_table_breaking_values(data):
    data.by_service[0]["key"] = "a|b<i>"
    text = render_markdown(data)
    assert "a\\|b&lt;i&gt;" in text


def test_html_is_self_contained_with_sections(data):
    html = render_html(data)
    parsed = _parse(html)
    assert "script" not in parsed.tags
    assert "<script" not in html.lower()
    for name, value in parsed.attrs:
        if name in ("src", "href"):
            pytest.fail(f"external reference {name}={value!r}")
    assert not re.search(r"url\(|@import|https?://", html)
    for section in SECTIONS:
        assert section in parsed.headings
    assert "$125.35" in html
    assert "+4.5%" in html
    assert "width: 63.1%" in html


def test_html_escapes_tag_values():
    df = load_cur(FIXTURE)
    df.loc[df["tag_team"] == "platform", "tag_team"] = "<b>platform</b>"
    html = render_html(build_report_data(df, ["team"], {}))
    assert "<b>" not in html
    assert "&lt;b&gt;platform&lt;/b&gt;" in html
    assert "b" not in _parse(html).tags


def test_cli_report_writes_both_files(tmp_path):
    out = tmp_path / "out"
    result = runner.invoke(app, ["report", str(FIXTURE), "--format", "both", "--out", str(out)])
    assert result.exit_code == 0, result.output
    for name in ("cost-report.md", "cost-report.html"):
        path = out / name
        assert path.is_file()
        assert path.stat().st_size > 0
        assert name in result.output


def test_cli_report_single_format(tmp_path):
    result = runner.invoke(
        app, ["report", str(FIXTURE), "--format", "md", "--out", str(tmp_path), "--tag", "team"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cost-report.md").is_file()
    assert not (tmp_path / "cost-report.html").exists()


def test_cli_report_rejects_unknown_format(tmp_path):
    result = runner.invoke(app, ["report", str(FIXTURE), "--format", "pdf", "--out", str(tmp_path)])
    assert result.exit_code != 0
