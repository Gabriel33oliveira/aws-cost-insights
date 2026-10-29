"""Command-line interface for AWS Cost Insights."""

from datetime import datetime
from pathlib import Path

import typer

from cost_insights import __version__
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
