# AWS Cost Insights

Offline CLI that analyzes AWS Cost and Usage Report CSVs and shows where your cloud money goes. No credentials needed.

AWS Cost Insights is a Python command-line tool that reads AWS Cost and Usage Report (CUR) style CSV exports and explains where the money goes. It breaks spend down by service, linked account, region and cost-allocation tag, and shows month-over-month trends. It also ranks the top cost drivers, reports untagged spend, and flags sudden daily cost jumps with a simple statistical anomaly detector. Everything runs offline. A built-in synthetic CUR generator produces realistic, reproducible sample data, so you can try the tool and run the full test suite without an AWS account. Results print as terminal tables or get written to Markdown and self-contained HTML reports.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # or: pip install -e .[dev]
.venv/Scripts/python -m cost_insights version
.venv/Scripts/python -m pytest -q
```

On macOS or Linux, use `.venv/bin/python` in place of `.venv/Scripts/python`.

## Synthetic data

You don't need an AWS account to try the tool. The `generate` command writes a CUR-style CSV with daily usage for six services, three regions and as many linked accounts as you ask for. The data has a mild monthly growth trend, cheaper weekends, `team` and `env` tags (about 15% of rows are untagged), a few Credit rows and, unless you pass `--no-anomalies`, two injected cost spikes. The same seed always produces the same file.

```bash
.venv/Scripts/python -m cost_insights generate --out data/samples/my_cur.csv --start 2026-01-01 --months 3 --accounts 3 --seed 42
```

A small sample (2 months, 2 accounts, seed 42) is committed at `data/samples/sample_cur.csv`.
