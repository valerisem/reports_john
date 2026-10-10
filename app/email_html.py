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
MGR_MIN_PX = 16
MGR_RANGE_PX = 44

# Five, not ten: the weekly progress section above took the room.
RETAINED_SHOWN = 5

# Weekly progress: verdict colours (text, tint) and the bar charts.
UP = ("#15803d", "#e7f5ec")
FLAT = ("#a16207", "#fdf4e2")
DOWN = ("#b91c1c", "#fdecec")
CHART_PX = 64
# History bars are a tint of the series colour; the live bar is full strength.
SERIES = {
    "pink": (ACCENT_PINK, "#f6b8da"),
    "purple": (ACCENT_PURPLE, "#cdbcf6"),
}

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=False,
    lstrip_blocks=False,
)


def _contact_ytd(data: ReportData, brand, with_poc: bool) -> str:
    """Won so far this year on deals with this brand's main contact."""
    if not with_poc or not brand.contacts:
        return ""
    won = data.ytd_for_contact(brand.contacts[0])
    return compact_gbp(won) if won else ""


def _margin_label(brand) -> str:
    """Gross margin, starred when it is a forecast rather than a final cost."""
    if brand.margin is None:
        return ""
    return percent(brand.margin) + ("*" if brand.margin_is_forecast else "")


def _brand_entries(data: ReportData, status: str, with_poc: bool, limit: int = 10) -> list[dict]:
    entries = []
    for brand in data.top_brands(status, limit=limit):
        entries.append(
            {
                "name": brand.name,
                "url": website_url(brand.website),
                "value": compact_gbp(brand.weighted_gbp),
                "margin": _margin_label(brand),
                "poc": brand.contacts[0] if (with_poc and brand.contacts) else "",
                "poc_ytd": _contact_ytd(data, brand, with_poc),
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


def _won_label(data: "ReportData | None", method: str, name: str) -> str:
    """Won so far this year, or nothing at all.

    A zero would read as a bad year rather than a year no one has closed in
    yet, so an empty string keeps the line off the page entirely.
    """
    if data is None or not name:
        return ""
    won = getattr(data, method)(name)
    return compact_gbp(won) if won else ""


def _photo(name: str, template: str) -> str:
    """The cut-out head for a person, named after their first name.

    'Emma-Leigh Pedder' resolves to emma.png: the bucket is keyed on the plain
    first name, so anything after a hyphen is dropped.
    """
    if not template:
        return ""
    slug = first_name(name).split("-")[0].lower()
    return template.format(name=slug)


def _leaderboard(owner_rows: list[dict], pod_rows: list[dict],
                 photo_template: str, data: ReportData | None = None) -> list[dict]:
    """The three pods, best first, each with the managers inside it.

    The pod total is every deal the lead owns; a manager's figure is the slice
    of that same pipeline they manage, so the managers never sum to the total.
    What is left is the lead's own, shown as 'self-managed' - together they do
    account for the whole pod.

    Column height is proportional to the pod total and manager bars are scaled
    against the largest manager anywhere, so the small bars compare managers to
    each other rather than to the column above them.
    """
    ranked = sorted(owner_rows, key=lambda r: -r["weighted_gbp"])[:3]
    if not ranked:
        return []
    top = ranked[0]["weighted_gbp"] or 1

    managers_by_owner: dict[str, list[dict]] = {}
    for row in pod_rows:
        if row["manager"]:
            managers_by_owner.setdefault(row["owner"], []).append(row)
    am_top = max(
        (r["weighted_gbp"] for rows in managers_by_owner.values() for r in rows),
        default=0,
    ) or 1

    entries = []
    for place, row in enumerate(ranked):
        fill, numeral = PLACE_FILLS[min(place, len(PLACE_FILLS) - 1)]
        mine = sorted(
            managers_by_owner.get(row["name"], []),
            key=lambda r: -r["weighted_gbp"],
        )
        direct = row["weighted_gbp"] - sum(r["weighted_gbp"] for r in mine)
        entries.append(
            {
                "name": first_name(row["name"]),
                "owner": row["name"],
                "rank": place + 1,
                "total": compact_gbp(row["weighted_gbp"]),
                "deals": row["deals"],
                "direct": compact_gbp(direct),
                "direct_percent": round(direct / (row["weighted_gbp"] or 1) * 100),
                "colour": fill,
                "numeral_colour": numeral,
                "height": round(
                    PODIUM_MIN_PX + (row["weighted_gbp"] / top) * PODIUM_RANGE_PX
                ),
                # The phone layout lays the same figures out as horizontal
                # bars, which need a percentage rather than a pixel height.
                "bar_percent": bar_percent(row["weighted_gbp"], top),
                "photo": _photo(row["name"], photo_template),
                # Won so far this year by the same person, so the podium shows
                # what landed beside what is still only forecast.
                # Three separate figures: the pod's whole book, the lead's own
                # deals, and each manager's own deals.
                "pod_ytd": _won_label(data, "ytd_for_pod", row["name"]),
                "ytd": _won_label(data, "ytd_for_person", row["name"]),
                "managers": [
                    {
                        "name": first_name(r["manager"]),
                        "value": compact_gbp(r["weighted_gbp"]),
                        "height": round(
                            MGR_MIN_PX + (r["weighted_gbp"] / am_top) * MGR_RANGE_PX
                        ),
                        "bar_percent": bar_percent(r["weighted_gbp"], am_top),
                        "photo": _photo(r["manager"], photo_template),
                        "ytd": _won_label(data, "ytd_for_person", r["manager"]),
                    }
                    for r in mine
                ],
            }
        )
    return entries


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


def _change(now: float, before: float, threshold: float) -> int:
    """1 up, -1 down, 0 within ``threshold`` (a share of the earlier figure)."""
    if before == 0:
        return 0 if now == 0 else 1
    shift = (now - before) / abs(before)
    return 0 if abs(shift) < threshold else (1 if shift > 0 else -1)


def _signed_gbp(value: float) -> str:
    return ("+" if value >= 0 else "\u2212") + compact_gbp(abs(value))


def _signed(value: int) -> str:
    return f"+{value}" if value >= 0 else f"\u2212{abs(value)}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _verdict(direction: int, up: str, flat: str, down: str) -> dict:
    colour, tint = {1: UP, 0: FLAT, -1: DOWN}[direction]
    return {
        "arrow": {1: "\u25b2", 0: "\u25cf", -1: "\u25bc"}[direction],
        "word": {1: up, 0: flat, -1: down}[direction],
        "colour": colour,
        "tint": tint,
    }


def _chart(values: list[float], dates: list, *, series: str, top_label: str) -> dict:
    """Zero-based bars, one per week, the live position last and darkest."""
    full, tint = SERIES[series]
    peak = max(values, default=0)
    bars = []
    for index, value in enumerate(values):
        height = round(CHART_PX * value / peak) if peak and value > 0 else 0
        bars.append({
            "height": max(height, 2) if value > 0 else 0,
            "colour": full if index == len(values) - 1 else tint,
        })
    return {
        "bars": bars,
        "width": round(100 / len(values), 3) if values else 100,
        "height": CHART_PX,
        "top_label": top_label,
        "start_label": dates[0].strftime("%-d %b") if dates else "",
        "end_label": "Now",
    }


def _progress_section(progress) -> dict | None:
    """John's three questions, each answered in a line before any chart."""
    if progress is None:
        return None
    rates = progress.rates
    shown = progress.shown
    now = progress.now
    before = progress.last_week
    since = before.week_ending.strftime("%-d %b") if before else ""
    blocks = []

    # 1. New-brand pipeline, number and value.
    weighted = now.new.weighted_gbp(rates)
    series = shown + [now]
    lines = []
    if before is not None:
        was_weighted = before.new.weighted_gbp(rates)
        deals_dir = _change(now.new.deals, before.new.deals, 0.02)
        value_dir = _change(weighted, was_weighted, 0.02)
        direction = (
            1 if deals_dir >= 0 and value_dir >= 0 and (deals_dir or value_dir)
            else -1 if deals_dir <= 0 and value_dir <= 0 and (deals_dir or value_dir)
            else 0
        )
        lines.append(
            f"Since w/e {since}: {_signed(now.new.deals - before.new.deals)} deals, "
            f"{_signed_gbp(weighted - was_weighted)} weighted"
        )
    else:
        direction = 0
    if progress.flow is not None:
        flow = progress.flow
        lines.append(
            f"Moved since {since}: {flow.added} added ({compact_gbp(flow.added_gbp)}) "
            f"\u00b7 {flow.won} won ({compact_gbp(flow.won_gbp)}) "
            f"\u00b7 {flow.lost} lost ({compact_gbp(flow.lost_gbp)})"
        )
    if shown:
        first = shown[0]
        lines.append(
            f"{len(shown)} weeks ago: {_plural(first.new.deals, 'deal')}, "
            f"{compact_gbp(first.new.weighted_gbp(rates))} weighted"
        )
    values = [p.new.weighted_gbp(rates) for p in series]
    counts = [float(p.new.deals) for p in series]
    dates = [p.week_ending for p in series]
    blocks.append({
        "question": "Is the new-brand pipeline growing?",
        "verdict": _verdict(direction, "Growing", "Steady", "Shrinking"),
        "headline": f"{_plural(now.new.deals, 'open deal')} with new brands \u00b7 "
                    f"{compact_gbp(weighted)} weighted",
        "lines": lines,
        "charts": [
            _chart(values, dates, series="pink",
                   top_label=f"Weighted value \u00b7 peak {compact_gbp(max(values, default=0))}"),
            _chart(counts, dates, series="purple",
                   top_label=f"Open deals \u00b7 peak {round(max(counts, default=0))}"),
        ],
    })

    # 2. Win rate by value, rolling window.
    current = progress.win_rates[-1]
    by_date = {w.on: w for w in progress.win_rates}
    previous = by_date.get(before.week_ending) if before else None
    oldest = progress.win_rates[0] if len(progress.win_rates) > 1 else None
    lines = []
    if current.rate is None:
        direction = 0
        headline = f"No deals closed in the last {progress.win_rate_weeks} weeks"
    else:
        headline = (f"{percent(current.rate)} of closed value won over the last "
                    f"{progress.win_rate_weeks} weeks")
        if previous is not None and previous.rate is not None:
            gap = current.rate - previous.rate
            direction = 0 if abs(gap) < 0.01 else (1 if gap > 0 else -1)
            lines.append(f"W/e {since}: {percent(previous.rate)}")
        else:
            direction = 0
        lines.append(f"{compact_gbp(current.won_gbp)} won, {compact_gbp(current.lost_gbp)} "
                     f"lost in those {progress.win_rate_weeks} weeks")
    if oldest is not None and oldest.rate is not None:
        lines.append(f"{len(shown)} weeks ago: {percent(oldest.rate)}")
    rates_shown = [w.rate or 0.0 for w in progress.win_rates]
    blocks.append({
        "question": "Is the win rate holding?",
        "verdict": _verdict(direction, "Improving", "Holding", "Slipping"),
        "headline": headline,
        "lines": lines,
        "charts": [
            _chart(rates_shown, [w.on for w in progress.win_rates], series="purple",
                   top_label=f"Win rate by value, rolling {progress.win_rate_weeks} weeks "
                             f"\u00b7 peak {percent(max(rates_shown, default=0))}"),
        ],
    })

    # 3. Big clients coming back for more.
    clients = progress.clients
    committed = sum(1 for c in clients if c.open_deals)
    if not clients:
        direction = 0
    elif committed * 5 >= len(clients) * 4:
        direction = 1
    elif committed * 2 >= len(clients):
        direction = 0
    else:
        direction = -1
    blocks.append({
        "question": "Are our biggest clients committing to more?",
        "verdict": _verdict(direction, "Recommitting", "Mixed", "At risk"),
        "headline": (
            f"{committed} of our top {len(clients)} clients "
            f"{'has' if committed == 1 else 'have'} another programme in the pipeline"
            if clients else "No deals won in the last 12 months"
        ),
        "lines": [],
        "charts": [],
        "clients": [
            {
                "name": c.name,
                "won": compact_gbp(c.won_gbp),
                "programmes": f"{_plural(c.programmes, 'programme')} this year "
                              f"\u00b7 {c.programmes_before} the year before",
                "trend": _verdict(_change(c.programmes, c.programmes_before, 0.01), "", "", ""),
                "open": (f"{_plural(c.open_deals, 'deal')} open \u00b7 "
                         f"{compact_gbp(c.open_weighted_gbp)} weighted")
                        if c.open_deals else "Nothing in the pipeline",
                "at_risk": not c.open_deals,
            }
            for c in clients
        ],
    })

    return {
        "subtitle": (f"Each week compared with the week ending {before.week_ending.strftime('%-d %B')}"
                     if before else "Week by week"),
        "blocks": blocks,
        "footnote": (
            "New brands are those we have never won a deal with. Bars run oldest to newest, "
            f"one per week over {len(shown)} weeks, ending with today. Win rate is won value "
            f"divided by won plus lost value. Top clients are ranked by value won in the last "
            "12 months; a programme is a won deal."
        ),
    }


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

    leaderboard = _leaderboard(owner_rows, data.by_pod(), owner_photo_url_template, data)
    split = data.by_stage_owner()
    stage_bars = _bar_rows(stage_rows, "label", "value_gbp", max_fill=94)
    for bar, row in zip(stage_bars, stage_rows):
        bar["segments"] = _stage_segments(
            row["stage"], row["value_gbp"], bar["percent"], split, leaderboard
        )

    top_existing = _brand_entries(data, EXISTING, with_poc=False, limit=RETAINED_SHOWN)
    # Only brands John has not been told about before, so the same names are
    # not re-announced week after week. Often this is empty.
    def entry(brand) -> dict:
        return {
            "margin": _margin_label(brand),
            "name": brand.name,
            "url": website_url(brand.website),
            "value": compact_gbp(brand.weighted_gbp),
            "poc": brand.contacts[0] if brand.contacts else "",
            "poc_ytd": _contact_ytd(data, brand, True),
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
        "progress": _progress_section(data.progress),
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
            {"heading": f"Top {RETAINED_SHOWN} retained clients", "brands": top_existing},
            {
                "heading": f"New this {period_label}",
                "brands": top_new,
                "empty_note": "No new brands since the last update.",
                "extra_heading": "Top new business in the pipeline" if fallback else "",
                "extra_brands": fallback,
            },
        ],
        "margin_note": (
            "Percentages are average gross margin on delivered campaigns. "
            "* means no campaign was delivered fully with client yet so margins "
            "are based on forecasted gross margin."
            if data.has_forecast_margin
            else "Percentages are average gross margin on delivered campaigns."
        ) if any(b.margin is not None for b in data.brands) else "",
        "remaining_brands": max(0, len(data.brands) - shown),
        "leaderboard": {
            "heading": "Weighted Pipeline Leaderboard",
            "entries": leaderboard,
            # A blank is meaningful here, so say what it means rather than
            # leaving it to be read as a missing number.
            "ytd_note": (
                "Year to date is deals won since "
                f"{data.financial_year_start.strftime('%-d %B %Y')} that the person "
                "owns in Pipedrive. No figure means none won yet."
                if data.financial_year_start and data.won_ytd else ""
            ),
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
    ]
    section = _progress_section(data.progress)
    if section:
        lines += ["How we're tracking", section["subtitle"]]
        for block in section["blocks"]:
            lines += ["", block["question"],
                      f"  {block['verdict']['word']}: {block['headline']}"]
            lines += [f"  {line}" for line in block["lines"]]
            lines += [f"  {c['name']}: {c['won']} won, {c['programmes']}, {c['open']}"
                      for c in block.get("clients", [])]
        lines.append("")
    lines += [
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
