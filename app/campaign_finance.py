"""Delivered-campaign profitability, read from Supabase.

Pipedrive's own "Projected Margin (%)" is a planning figure a campaign
manager types in at proposal stage, so it is not used here. The real numbers
live in Supabase:

* ``campaigns``        - one row per Pipedrive deal (``pd_deal_id``), carrying
                         the budget and the paid media / brand uplift spend.
* ``creator_bookings`` - one row per creator per campaign, with the fee
                         actually paid. ``campaign_number`` IS the Pipedrive
                         deal id when it is numeric; rows prefixed ``board:``
                         are creators listed on a client board who were never
                         paid, and carry no fee at all, so they are ignored.

Cost is influencer fees + paid media + brand uplift. ``campaigns.final_cost_gbp``
is deliberately untouched: 161 of its 250 values are zero and it understates
the creator payments in 182 of the 201 rows where both exist.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .brands import Brand, is_placeholder, normalise_name
from .team_directory import SupabaseClient, TeamDirectoryError

log = logging.getLogger(__name__)

# Only a delivered campaign has a final cost; one still running has spent part
# of its budget and would read as wildly profitable.
DELIVERED = "Delivered"
PAGE = 1000


@dataclass
class CampaignFinance:
    pd_deal_id: int
    client_name: str
    stage: str
    revenue_gbp: float
    influencer_cost_gbp: float | None
    paid_media_gbp: float = 0.0
    brand_uplift_gbp: float = 0.0
    # True when the cost came from creator payment records rather than the
    # board's running spend total, which is the more reliable of the two.
    costed_from_payments: bool = False

    @property
    def is_delivered(self) -> bool:
        return self.stage == DELIVERED

    @property
    def has_cost(self) -> bool:
        return self.influencer_cost_gbp is not None and self.revenue_gbp > 0

    @property
    def total_cost_gbp(self) -> float:
        return (self.influencer_cost_gbp or 0.0) + self.paid_media_gbp + self.brand_uplift_gbp


@dataclass
class BrandFinance:
    """Delivered campaigns for one brand, rolled up."""
    campaigns: int = 0
    revenue_gbp: float = 0.0
    cost_gbp: float = 0.0
    costed_from_payments: int = 0

    @property
    def gross_profit_gbp(self) -> float:
        return self.revenue_gbp - self.cost_gbp

    @property
    def margin(self) -> float | None:
        """Gross margin as 0..1, or None when there is nothing to measure."""
        if self.revenue_gbp <= 0 or not self.campaigns:
            return None
        return self.gross_profit_gbp / self.revenue_gbp


@dataclass
class CampaignFinanceSet:
    by_deal: dict[int, CampaignFinance] = field(default_factory=dict)
    loaded: bool = False

    def roll_up(self, deal_ids: set[int]) -> BrandFinance:
        """Total the delivered, costed campaigns behind a set of deals."""
        total = BrandFinance()
        for deal_id in deal_ids:
            campaign = self.by_deal.get(deal_id)
            if campaign is None or not campaign.is_delivered or not campaign.has_cost:
                continue
            total.campaigns += 1
            total.revenue_gbp += campaign.revenue_gbp
            total.cost_gbp += campaign.total_cost_gbp
            total.costed_from_payments += 1 if campaign.costed_from_payments else 0
        return total


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _paged(client: SupabaseClient, table: str, select: str) -> list[dict]:
    """PostgREST caps a response, so walk it in pages."""
    out: list[dict] = []
    offset = 0
    while True:
        page = client.rows_range(table, select, offset, PAGE)
        out.extend(page)
        if len(page) < PAGE:
            return out
        offset += PAGE


def build_finance(campaign_rows: list[dict], booking_rows: list[dict]) -> CampaignFinanceSet:
    # Creator fees, summed per Pipedrive deal. A booking with no fee was never
    # paid, so it adds nothing but must not turn a missing cost into zero.
    fees: dict[int, float] = {}
    for row in booking_rows:
        number = str(row.get("campaign_number") or "").strip()
        if not number.isdigit():
            continue  # 'board:...' and 'pay:...' rows carry no fee
        fee = _as_float(row.get("fee_gbp"))
        if fee is None:
            continue
        fees[int(number)] = fees.get(int(number), 0.0) + fee

    by_deal: dict[int, CampaignFinance] = {}
    for row in campaign_rows:
        deal_id = row.get("pd_deal_id")
        if deal_id is None:
            continue
        deal_id = int(deal_id)
        from_payments = deal_id in fees
        cost = fees.get(deal_id)
        if cost is None:
            # Fall back to the board's running spend, which tracks the creator
            # payments closely but is not itself a payment record.
            cost = _as_float(row.get("current_spend_gbp"))
        by_deal[deal_id] = CampaignFinance(
            pd_deal_id=deal_id,
            client_name=(row.get("client_name") or "").strip(),
            stage=(row.get("stage") or "").strip(),
            revenue_gbp=_as_float(row.get("budget_gbp")) or 0.0,
            influencer_cost_gbp=cost,
            paid_media_gbp=_as_float(row.get("paid_media_spend")) or 0.0,
            brand_uplift_gbp=_as_float(row.get("brand_uplift_spend")) or 0.0,
            costed_from_payments=from_payments,
        )
    return CampaignFinanceSet(by_deal=by_deal, loaded=True)


def load_campaign_finance(url: str, api_key: str) -> CampaignFinanceSet:
    """Best effort: profitability is an extra, never a reason to fail a send."""
    if not url or not api_key:
        log.info("Supabase not configured; campaign profitability omitted.")
        return CampaignFinanceSet()
    try:
        with SupabaseClient(url, api_key) as client:
            campaigns = _paged(
                client, "campaigns",
                "pd_deal_id,client_name,stage,budget_gbp,current_spend_gbp,"
                "paid_media_spend,brand_uplift_spend",
            )
            bookings = _paged(client, "creator_bookings", "campaign_number,fee_gbp")
        return build_finance(campaigns, bookings)
    except (TeamDirectoryError, Exception) as exc:  # noqa: BLE001 - never fatal
        log.warning("Campaign profitability could not be loaded: %s", exc)
        return CampaignFinanceSet()


def _domain_label(domain: str) -> str:
    """opera.com -> 'opera'. The label a client is usually called by.

    Brand domains arrive normalised, but a stray 'www.' would otherwise index
    every brand under the same useless label.
    """
    if not domain:
        return ""
    cleaned = domain.strip().lower().removeprefix("www.")
    return normalise_name(cleaned.split(".")[0])


def name_index(brands: dict[int, Brand]) -> dict[str, str]:
    """Normalised client name -> brand key, for campaigns with no usable deal.

    A name that would point at two different brands is dropped rather than
    guessed at: a wrong merge is far worse than a missing figure.
    """
    candidates: dict[str, set[str]] = {}
    for brand in brands.values():
        if not brand.key:
            continue
        labels = {normalise_name(name) for name in brand.names}
        labels |= {_domain_label(domain) for domain in brand.domains}
        for label in labels:
            if label and not is_placeholder(label):
                candidates.setdefault(label, set()).add(brand.key)
    return {label: keys.pop() for label, keys in candidates.items() if len(keys) == 1}
