#!/usr/bin/env python3
"""Render the email and workbook locally, without sending anything.

    python scripts/preview_local.py            # uses live Pipedrive data
    python scripts/preview_local.py --fixture  # uses the bundled sample data

Writes out/email.html and out/report.xlsx.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.report import build, collect  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true",
                        help="use the bundled reference data instead of Pipedrive")
    args = parser.parse_args()

    settings = get_settings()
    if args.fixture:
        from app.model import build_report
        from tests import fixture
        data = build_report(**fixture.load())
    else:
        data = collect(settings)

    artefacts = build(settings, data)
    out = Path("out")
    out.mkdir(exist_ok=True)
    (out / "email.html").write_text(artefacts.html, encoding="utf-8")
    (out / "report.xlsx").write_bytes(artefacts.workbook)

    print(f"Report date     : {data.report_date}")
    print(f"Open deals      : {data.open_deal_count}")
    print(f"Brands          : {len(data.brands)}")
    print(f"Pipeline        : £{data.pipeline_gbp:,.0f}")
    print(f"Weighted        : £{data.weighted_gbp:,.0f}")
    print(f"FX rates        : {data.rates} ({'live' if data.rates_are_live else 'fallback'})")
    print(f"Would send to   : {artefacts.to} cc {artefacts.cc}")
    print(f"Subject         : {artefacts.subject}")
    print("\nWrote out/email.html and out/report.xlsx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
