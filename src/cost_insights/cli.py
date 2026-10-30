"""Command-line interface for AWS Cost Insights."""

from datetime import datetime
from pathlib import Path

import math

import typer
from rich.console import Console
from rich.table import Table

from cost_insights import __version__
from cost_insights.analysis import (
    monthly_trend,
    spend_by,
    top_drivers,
    total_spend,
    untagged_spend,
    validate_dimension,
)
from cost_insights.loader import load_cur
from cost_insights.synthetic import generate_cur, write_cur_csv

app = typer.Typer(
    name="cost-insights",
    help="Analyze AWS Cost and Usage Report CSVs offline.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Analyze AWS Cost and Usage Report CSVs offline."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def generate(
    out: Path = typer.Option(Path("data/samples/sample_cur.csv"), "--out", help="Output CSV path."),
    start: datetime = typer.Option(
        "2026-01-01", "--start", formats=["%Y-%m-%d"], help="First day of the data (YYYY-MM-DD)."
    ),
    months: int = typer.Option(3, "--months", min=1, help="Number of calendar months."),
    accounts: int = typer.Option(3, "--accounts", min=1, help="Number of linked accounts."),
    seed: int = typer.Option(42, "--seed", help="Random seed; the same seed gives the same data."),
    no_anomalies: bool = typer.Option(False, "--no-anomalies", help="Do not inject cost spikes."),
) -> None:
    """Generate a synthetic CUR CSV file."""
    df = generate_cur(start.date(), months, accounts, seed, anomalies=not no_anomalies)
    write_cur_csv(df, out)
    typer.echo(f"Wrote {len(df)} rows to {out}")
    for detail in df.attrs["anomaly_details"]:
        typer.echo(
            f"Injected anomaly: {detail['date']} {detail['service']} x{detail['factor']:g} "
            f"in account {detail['account_id']}"
        )


@app.command()
def summary(
    path: Path = typer.Argument(..., help="CUR CSV file or directory of CSV files."),
    by: list[str] = typer.Option(
        ["service"],
        "--by",
        help="Breakdown dimension: service, account_id, region or tag:<key>. Repeatable.",
    ),
    top: int = typer.Option(10, "--top", min=1, help="Rows per breakdown and top drivers."),
    tag: list[str] = typer.Option(
        None,
        "--tag",
        help="Tag key that spend must have to count as tagged. Repeatable; "
        "defaults to every tag in the data.",
    ),
) -> None:
    """Print spend breakdowns, month-over-month trend, top drivers and untagged spend."""
    for dimension in by:
        try:
            validate_dimension(dimension)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--by") from exc

    df = load_cur(path)
    console = Console()

    console.print(f"[bold]Total spend:[/bold] {_money(total_spend(df))}")

    for dimension in by:
        table = Table(title=f"Spend by {dimension}")
        table.add_column(dimension)
        table.add_column("Cost", justify="right")
        table.add_column("Share", justify="right")
        for row in spend_by(df, dimension, top=top).itertuples():
            table.add_row(str(row.key), _money(row.cost), _pct(row.share))
        console.print(table)

    trend = Table(title="Month-over-month trend")
    trend.add_column("Month")
    for name in ("Cost", "Change", "Change %"):
        trend.add_column(name, justify="right")
    for row in monthly_trend(df).itertuples():
        trend.add_row(
            row.month,
            _money(row.cost),
            _money(row.change, signed=True),
            _pct(row.pct_change, signed=True),
        )
    console.print(trend)

    drivers = top_drivers(df, top=top)
    if drivers.empty:
        console.print("Top cost drivers: need at least two months of data.")
    else:
        table = Table(title="Top cost drivers (last two months)")
        for name in ("Service", "Account", "Region"):
            table.add_column(name)
        for name in ("Previous", "Current", "Delta"):
            table.add_column(name, justify="right")
        for row in drivers.itertuples():
            table.add_row(
                row.service,
                row.account_id,
                row.region,
                _money(row.previous_cost),
                _money(row.current_cost),
                _money(row.delta, signed=True),
            )
        console.print(table)

    tag_keys = tag or sorted(c[len("tag_"):] for c in df.columns if c.startswith("tag_"))
    if not tag_keys:
        console.print("Untagged spend: no tag columns in the data.")
        return
    untagged = untagged_spend(df, tag_keys)
    table = Table(title=f"Untagged spend (missing any of: {', '.join(tag_keys)})")
    table.add_column("Service")
    table.add_column("Cost", justify="right")
    table.add_column("Share of untagged", justify="right")
    for row in untagged["by_service"].itertuples():
        table.add_row(row.service, _money(row.cost), _pct(row.share))
    console.print(table)
    console.print(
        f"[bold]Untagged total:[/bold] {_money(untagged['cost'])} "
        f"({_pct(untagged['share'])} of total spend)"
    )


def _money(value: float, signed: bool = False) -> str:
    if math.isnan(value):
        return "-"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    return f"{sign}${abs(value):,.2f}"


def _pct(value: float, signed: bool = False) -> str:
    if math.isnan(value):
        return "-"
    return f"{value:+.1%}" if signed else f"{value:.1%}"
