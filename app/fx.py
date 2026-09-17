"""Live GBP conversion rates, with a configured fallback."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


def get_rates(api_url: str, fallback_usd: float, fallback_eur: float) -> tuple[dict[str, float], bool]:
    """Return ({currency: rate_to_GBP}, live) for GBP, USD and EUR.

    The API is queried as GBP -> USD,EUR, so the rates come back inverted and
    are reciprocated here. Any failure falls back to the configured rates so a
    scheduled report never fails to send because an FX endpoint is down.
    """
    rates = {"GBP": 1.0, "USD": fallback_usd, "EUR": fallback_eur}
    try:
        response = httpx.get(api_url, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
        quoted = payload.get("rates") or {}
        live = {}
        for currency in ("USD", "EUR"):
            per_gbp = quoted.get(currency)
            if isinstance(per_gbp, (int, float)) and per_gbp > 0:
                live[currency] = round(1.0 / float(per_gbp), 4)
        if len(live) == 2:
            rates.update(live)
            log.info("Using live FX rates: %s", rates)
            return rates, True
        log.warning("FX response incomplete (%r); using fallback rates", quoted)
    except Exception as exc:  # noqa: BLE001 - never block the report on FX
        log.warning("FX lookup failed (%s); using fallback rates", exc)
    return rates, False
