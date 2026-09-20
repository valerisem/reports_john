"""Supabase client names that need pointing at a Pipedrive brand by hand.

Most campaigns reach their brand through the Pipedrive deal, and the rest
through an exact name or domain match. What is left over is genuine spelling
drift between the two systems - "Match.com LLC" in Supabase against "Match
Group" in Pipedrive - which no safe automatic rule can bridge.

An alias maps one normalised Supabase client name to the normalised name (or
domain label) of the Pipedrive brand it belongs to. Nothing is guessed: an
alias exists because somebody decided those two names are the same client.

Set CLIENT_ALIASES to add more without a deploy, in either form:

    CLIENT_ALIASES=Match.com LLC = Match Group; Rocky Road Games = Rocky Road
    CLIENT_ALIASES={"Match.com LLC": "Match Group"}
"""
from __future__ import annotations

import json
import logging

from .brands import normalise_name

log = logging.getLogger(__name__)

# Confirmed against Pipedrive: both targets exist as organisations there.
DEFAULT_ALIASES: dict[str, str] = {
    "Match.com LLC": "Match Group",
    "Rocky Road Games": "Rocky Road",
}


def parse_aliases(raw: str) -> dict[str, str]:
    """Normalised alias map from the configured string. Never raises."""
    if not raw or not raw.strip():
        return {}
    pairs: dict[str, str] = {}
    text = raw.strip()
    if text.startswith("{"):
        try:
            loaded = json.loads(text)
        except ValueError as exc:
            log.warning("CLIENT_ALIASES is not valid JSON, ignoring it: %s", exc)
            return {}
        pairs = {str(k): str(v) for k, v in (loaded or {}).items()}
    else:
        for chunk in text.replace("\n", ";").split(";"):
            if "=" not in chunk:
                continue
            source, _, target = chunk.partition("=")
            if source.strip() and target.strip():
                pairs[source.strip()] = target.strip()

    out: dict[str, str] = {}
    for source, target in pairs.items():
        key, value = normalise_name(source), normalise_name(target)
        if key and value and key != value:
            out[key] = value
    return out


def alias_map(extra: str = "") -> dict[str, str]:
    """Defaults, with anything configured taking precedence."""
    merged = parse_aliases(
        "; ".join(f"{k} = {v}" for k, v in DEFAULT_ALIASES.items())
    )
    merged.update(parse_aliases(extra))
    return merged
