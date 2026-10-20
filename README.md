# AWS Cost Insights

Offline CLI that analyzes AWS Cost and Usage Report CSVs and shows where your cloud money goes. No credentials needed.

AWS Cost Insights is a Python command-line tool that reads AWS Cost and Usage Report (CUR) style CSV exports and explains where the money goes. It breaks spend down by service, linked account, region and cost-allocation tag, and shows month-over-month trends. It also ranks the top cost drivers, reports untagged spend, and flags sudden daily cost jumps with a simple statistical anomaly detector. Everything runs offline. A built-in synthetic CUR generator produces realistic, reproducible sample data, so you can try the tool and run the full test suite without an AWS account. Results print as terminal tables or get written to Markdown and self-contained HTML reports.
