"""Command-line interface for AWS Cost Insights."""

import typer

from cost_insights import __version__

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
