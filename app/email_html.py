"""Renders the newsletter HTML from the report data."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .formatting import bar_percent, compact_gbp, first_name, percent, website_url
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
OWNER_PHOTO_URL_TEMPLATE = (
    "https://tciqyupqtpbhremuprxe.supabase.co/storage/v1/object/public/"
    "reports%20for%20john/{name}.png"
)

INK = "#15141f"
ACCENT_PINK = "#e91e8c"
ACCENT_PURPLE = "#7c3aed"

# Leaderboard places, best first. The same three fills colour the stacked
# stage bars, so a bar segment and its owner's podium column always match.
PLACE_FILLS = [
    (ACCENT_PINK, "#ffffff"),
    ("#8b5cf6", "#ffffff"),
    ("#c4b5fd", "#2b2a35"),
]
PODIUM_MIN_PX = 78
PODIUM_RANGE_PX = 94

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
                "margin": percent(brand.margin) if brand.margin is not None else "",
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


def _leaderboard(owner_rows: list[dict], photo_template: str) -> list[dict]:
    """Account owners ranked by weighted value, laid out 2nd - 1st - 3rd.

    Column height is proportional to weighted value, so the podium moves on
    its own each week. Only the top three place, which is what the layout has
    room for.
    """
    ranked = sorted(owner_rows, key=lambda r: -r["weighted_gbp"])[:3]
    if not ranked:
        return []
    top = ranked[0]["weighted_gbp"] or 1
    entries = []
    for place, row in enumerate(ranked):
        fill, numeral = PLACE_FILLS[min(place, len(PLACE_FILLS) - 1)]
        name = first_name(row["name"])
        entries.append(
            {
                "name": name,
                "rank": place + 1,
                "value": compact_gbp(row["weighted_gbp"]),
                "deals": row["deals"],
                "colour": fill,
                "numeral_colour": numeral,
                "height": round(
                    PODIUM_MIN_PX + (row["weighted_gbp"] / top) * PODIUM_RANGE_PX
                ),
                "photo": photo_template.format(name=name.lower())
                if photo_template
                else "",
                "owner": row["name"],
            }
        )
    # 2nd on the left, the winner in the middle, 3rd on the right
    order = [1, 0, 2]
    return [entries[i] for i in order if i < len(entries)]


def _stage_segments(stage: str, total: float, percent: int,
                    split: dict[str, dict[str, float]],
                    entries: list[dict]) -> list[dict]:
    """A stage bar cut into one segment per owner, in podium order.

    Widths are percentages of the whole row, not of the bar, so the template
    stays a single flat table row - nested percentage tables are unreliable in
    Outlook. Segment order is fixed across every stage so the bars can be read
    against each other.
    """
    owners = split.get(stage, {})
    if not total or not owners:
        return []
    segments = []
    used = 0
    for entry in entries:
        share = owners.get(entry["owner"], 0.0)
        if share <= 0:
            continue
        width = round(percent * share / total)
        if width <= 0:
            continue
        segments.append({"percent": width, "colour": entry["colour"]})
        used += width
    if not segments:
        return []
    # absorb any rounding drift into the last segment so the widths total up
    segments[-1]["percent"] += percent - used
    return [s for s in segments if s["percent"] > 0]


def render_email(
    data: ReportData,
    *,
    title: str,
    greeting_name: str,
    sender_name: str,
    test_banner: dict | None = None,
    new_brand_min: int = 3,
    period_label: str = "week",
    new_brand_fallback_count: int = 5,
    header_image_url: str = HEADER_IMAGE_URL,
    footer_image_url: str = FOOTER_IMAGE_URL,
    logo_url: str = LOGO_URL,
    owner_photo_url_template: str = OWNER_PHOTO_URL_TEMPLATE,
) -> str:
    stage_rows = [row for row in data.by_stage() if row["deals"]]
    owner_rows = [row for row in data.by_owner() if row["deals"]]

    leaderboard = _leaderboard(owner_rows, owner_photo_url_template)
    split = data.by_stage_owner()
    stage_bars = _bar_rows(stage_rows, "label", "value_gbp", max_fill=94)
    for bar, row in zip(stage_bars, stage_rows):
        bar["segments"] = _stage_segments(
            row["stage"], row["value_gbp"], bar["percent"], split, leaderboard
        )

    # Won so far this financial year, so the email shows what landed next to
    # what is still in play. Hidden entirely on a year with nothing won.
    ytd_section: list[dict] = []
    ytd_rows = [row for row in data.ytd_by_owner() if row["deals"]]
    if ytd_rows:
        fy_from = data.financial_year_start
        ytd_section = [
            {
                "heading": "Won year to date",
                "subtitle": f"{compact_gbp(data.won_ytd_gbp)} won by {len(ytd_rows)} owners",
                "footnote": (
                    "Closed won deals since "
                    f"{fy_from.strftime('%-d %B %Y')}." if fy_from else "Closed won deals."
                ),
                "colour": ACCENT_PURPLE,
                "rows": _bar_rows(ytd_rows, "name", "value_gbp", shorten_labels=True),
            }
        ]

    top_existing = _brand_entries(data, EXISTING, with_poc=False)
    # Only brands John has not been told about before, so the same names are
    # not re-announced week after week. Often this is empty.
    def entry(brand) -> dict:
        return {
            "margin": percent(brand.margin) if brand.margin is not None else "",
            "name": brand.name,
            "url": website_url(brand.website),
            "value": compact_gbp(brand.weighted_gbp),
            "poc": brand.contacts[0] if brand.contacts else "",
        }

    new_brands = data.brands_new_this_report[:10]
    top_new = [entry(b) for b in new_brands]

    # A quiet period would otherwise leave this column almost empty, so back
    # it with the biggest new business already in the pipeline - excluding
    # anything just listed above, which would read as a duplicate.
    fallback: list[dict] = []
    if len(new_brands) < new_brand_min:
        already = {b.name for b in new_brands}
        fallback = [
            entry(b)
            for b in data.top_brands(NEW_BUSINESS, limit=new_brand_fallback_count + len(already))
            if b.name not in already
        ][:new_brand_fallback_count]

    shown = len(top_existing) + len(top_new) + len(fallback)

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
            {
                "value": percent(data.new_business_share),
                "label": "New business",
                "colour": INK,
            },
        ],
        "brand_columns": [
            {"heading": "Top 10 retained clients", "brands": top_existing},
            {
                "heading": f"New this {period_label}",
                "brands": top_new,
                "empty_note": "No new brands since the last update.",
                "extra_heading": "Top new business in the pipeline" if fallback else "",
                "extra_brands": fallback,
            },
        ],
        "remaining_brands": max(0, len(data.brands) - shown),
        "leaderboard": {
            "heading": "Weighted Pipeline Leaderboard",
            "entries": leaderboard,
        },
        "bar_sections": [
            {
                "heading": "Pipeline by stage",
                "subtitle": f"{compact_gbp(data.pipeline_gbp)} total",
                # Pipedrive's own board shows these stages weighted by close
                # probability, so say plainly that these bars are not.
                "footnote": "Full deal value, not weighted by close probability.",
                "colour": ACCENT_PINK,
                "rows": stage_bars,
            },
            *ytd_section,
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
        "Weighted pipeline leaderboard",
    ]
    for place, row in enumerate(sorted(data.by_owner(), key=lambda r: -r["weighted_gbp"]), 1):
        lines.append(
            f"  {place}. {row['name']}: {compact_gbp(row['weighted_gbp'])} ({row['deals']} deals)"
        )
    lines += ["", "Pipeline by stage"]
    for row in data.by_stage():
        lines.append(f"  {row['label']}: {compact_gbp(row['value_gbp'])} ({row['deals']} deals)")
    lines += ["", "Kindly,", sender_name]
    return "\n".join(lines)
