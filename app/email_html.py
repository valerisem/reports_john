"""Renders the newsletter HTML from the report data."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .formatting import bar_percent, compact_gbp, first_name, website_url
from .model import EXISTING, NEW_BUSINESS, ReportData

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

HEADER_IMAGE_URL = (
    "https://tciqyupqtpbhremuprxe.supabase.co/storage/v1/object/public/"
    "reports%20for%20john/email-header_1.jpg"
)
FOOTER_IMAGE_URL = ""
LOGO_URL = (
    "https://tciqyupqtpbhremuprxe.supabase.co/storage/v1/object/public/"
    "reports%20for%20john/email-logo.png"
)

INK = "#15141f"
ACCENT_PINK = "#e91e8c"
ACCENT_PURPLE = "#7c3aed"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=False,
    lstrip_blocks=False,
)


def _brand_entries(data: ReportData, status: str, with_poc: bool) -> list[dict]:
    entries = []
    for brand in data.top_brands(status):
        entries.append(
            {
                "name": brand.name,
                "url": website_url(brand.website),
                "value": compact_gbp(brand.weighted_gbp),
                "poc": brand.contacts[0] if (with_poc and brand.contacts) else "",
            }
        )
    return entries


def _bar_rows(rows: list[dict], label_key: str, value_key: str, *,
              max_fill: int = 100, shorten_labels: bool = False) -> list[dict]:
    maximum = max((row[value_key] for row in rows), default=0)
    return [
        {
            "label": first_name(row[label_key]) if shorten_labels else row[label_key],
            "percent": bar_percent(row[value_key], maximum, max_fill),
            "value": compact_gbp(row[value_key]),
            "deals": row["deals"],
        }
        for row in rows
    ]


def render_email(
    data: ReportData,
    *,
    title: str,
    greeting_name: str,
    sender_name: str,
    test_banner: dict | None = None,
    header_image_url: str = HEADER_IMAGE_URL,
    footer_image_url: str = FOOTER_IMAGE_URL,
    logo_url: str = LOGO_URL,
) -> str:
    stage_rows = [row for row in data.by_stage() if row["deals"]]
    owner_rows = [row for row in data.by_owner() if row["deals"]]

    top_existing = _brand_entries(data, EXISTING, with_poc=False)
    top_new = _brand_entries(data, NEW_BUSINESS, with_poc=True)
    shown = len(top_existing) + len(top_new)

    context = {
        "title": title,
        "greeting_name": greeting_name,
        "sender_name": sender_name,
        "report_date": data.report_date.strftime("%-d %B %Y"),
        "preheader": (
            f"{data.open_deal_count} open deals · "
            f"{compact_gbp(data.weighted_gbp)} weighted pipeline"
        ),
        "header_image_url": header_image_url,
        "footer_image_url": footer_image_url,
        "logo_url": logo_url,
        "test_banner": test_banner,
        "stats": [
            {"value": f"{data.open_deal_count:,}", "label": "Open deals", "colour": INK},
            {"value": compact_gbp(data.weighted_gbp), "label": "Weighted", "colour": ACCENT_PINK},
            {"value": f"{data.new_brand_count:,}", "label": "New brands", "colour": INK},
        ],
        "brand_columns": [
            {"heading": "Top 10 clients", "brands": top_existing},
            {"heading": "Top 10 new business", "brands": top_new},
        ],
        "remaining_brands": max(0, len(data.brands) - shown),
        "bar_sections": [
            {
                "heading": "Pipeline by stage",
                "subtitle": f"{compact_gbp(data.pipeline_gbp)} total",
                "colour": ACCENT_PINK,
                "rows": _bar_rows(stage_rows, "label", "value_gbp", max_fill=94),
            },
            {
                "heading": "Weighted by account owner",
                "subtitle": None,
                "colour": ACCENT_PURPLE,
                "rows": _bar_rows(owner_rows, "name", "weighted_gbp", shorten_labels=True),
            },
        ],
    }
    return _env.get_template("email.html.j2").render(**context)


def plain_text_fallback(data: ReportData, greeting_name: str, sender_name: str) -> str:
    lines = [
        f"Hi {greeting_name}, here is the latest pipeline update. The full report is attached.",
        "",
        f"Open deals: {data.open_deal_count}",
        f"Weighted pipeline: {compact_gbp(data.weighted_gbp)}",
        f"Total pipeline: {compact_gbp(data.pipeline_gbp)}",
        f"New brands: {data.new_brand_count}",
        "",
        "Pipeline by stage",
    ]
    for row in data.by_stage():
        lines.append(f"  {row['label']}: {compact_gbp(row['value_gbp'])} ({row['deals']} deals)")
    lines += ["", "Weighted by account owner"]
    for row in data.by_owner():
        lines.append(f"  {row['name']}: {compact_gbp(row['weighted_gbp'])} ({row['deals']} deals)")
    lines += ["", "Kindly,", sender_name]
    return "\n".join(lines)
