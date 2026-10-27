"""Allow running the CLI with ``python -m cost_insights``."""

from cost_insights.cli import app

if __name__ == "__main__":
    app(prog_name="cost-insights")
