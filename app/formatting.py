"""Shared number/text formatting for the newsletter."""
from __future__ import annotations


def compact_gbp(value: float) -> str:
    """£4.35m / £992k / £450 - the shorthand used in the newsletter."""
    amount = float(value or 0)
    if abs(amount) >= 1_000_000:
        return f"£{amount / 1_000_000:.2f}m"
    if abs(amount) >= 1_000:
        return f"£{round(amount / 1_000):,.0f}k"
    return f"£{amount:,.0f}"


def full_gbp(value: float) -> str:
    return f"£{float(value or 0):,.0f}"


def bar_percent(value: float, maximum: float, max_fill: int = 100) -> int:
    """Bar width as a whole percentage of the row.

    ``max_fill`` is how wide the largest bar is drawn: the stage chart in the
    reference design leaves a little headroom at 94%, the owner chart runs the
    full width.
    """
    if not maximum:
        return 0
    return max(1, min(100, round(float(value) / float(maximum) * max_fill)))


def first_name(full_name: str) -> str:
    """'Valeriia Mukhai' -> 'Valeriia'. Account owners go by first name in the
    newsletter, while the workbook keeps the full name."""
    return (full_name or "").strip().split(" ")[0] or full_name


def percent(value: float) -> str:
    """0.512 -> '51%'. Never 0% or 100% unless it truly is."""
    pct = float(value or 0) * 100
    if 0 < pct < 1:
        return "<1%"
    if 99 < pct < 100:
        return ">99%"
    return f"{round(pct)}%"


def website_url(website: str) -> str:
    website = (website or "").strip()
    if not website:
        return ""
    if website.startswith(("http://", "https://")):
        return website
    return f"https://{website}"
