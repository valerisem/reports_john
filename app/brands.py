"""Resolving Pipedrive organisations into real brands.

Pipedrive accumulates duplicate organisation records for the same company:
"Dr Jart" exists twice, with two won deals on one record and the live open deal
on the other. Read per-organisation, that brand looks like new business when it
is a returning client.

Organisations are therefore clustered into brands, and won history is pooled
across the cluster. Matching is deliberately conservative — an identical
normalised name, or an identical website domain. Nothing fuzzy: wrongly merging
two genuinely different brands corrupts the report far worse than leaving a
duplicate in it.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Legal forms and filler that differ between records of the same company.
_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "corp", "corporation", "llc", "llp",
    "plc", "gmbh", "bv", "nv", "sa", "sas", "srl", "spa", "ag", "oy", "ab", "as",
    "pty", "pte", "co", "company", "group", "holdings", "holding", "int",
    "international", "global", "the",
}
_DOMAIN_STRIP = re.compile(r"^(?:https?://)?(?:www\.)?", re.I)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Dots and apostrophes join a word rather than split it, so "S.p.A." reads as
# one suffix and "Ollie's" matches "Ollies".
_JOINERS = re.compile(r"[.'’]")

# Values people type when there is no company. They are not brands, and
# clustering on them would fuse dozens of unrelated records into one.
_PLACEHOLDERS = {
    "", "na", "none", "no", "nocompany", "nonamecompany", "non", "donthave",
    "dont", "unknown", "unknowncompany", "test", "testing", "tbc", "tbd",
    "notapplicable", "nil", "null",
}


def normalise_name(name: str) -> str:
    """'Cetaphil Ltd.' and 'cetaphil' both become 'cetaphil'."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _JOINERS.sub("", text)
    words = [w for w in _NON_ALNUM.split(text) if w]
    kept = [w for w in words if w not in _SUFFIXES]
    return "".join(kept or words)


def normalise_domain(website: str) -> str:
    """'https://www.DrJart.com/uk' becomes 'drjart.com'."""
    text = (website or "").strip().lower()
    if not text:
        return ""
    text = _DOMAIN_STRIP.sub("", text)
    text = text.split("/")[0].split("?")[0].split("#")[0].strip()
    return text if "." in text else ""


@dataclass
class Brand:
    """One real company, backed by one or more Pipedrive organisations."""

    key: str
    name: str
    org_ids: list[int] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    domains: set[str] = field(default_factory=set)
    won_deals: int = 0

    @property
    def is_existing_client(self) -> bool:
        return self.won_deals > 0

    @property
    def is_duplicated(self) -> bool:
        return len(self.org_ids) > 1


class _Union:
    """Union-find over organisation ids."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def add(self, item: int) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: int) -> int:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:      # path compression
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


def is_placeholder(name: str) -> bool:
    return normalise_name(name) in _PLACEHOLDERS


def _display_name(names: list[str]) -> str:
    """The spelling most records use, then the shortest, then the tidiest.

    Shortest matters: "Olymptrade" is the brand, "Maree/Olymptrade" is one
    record's label for it, and the brand name is what reaches John.
    """
    # A record labelled "N/A" that joined by domain must not name the brand.
    real = [n for n in names if not is_placeholder(n)] or names
    counts: dict[str, int] = {}
    for name in real:
        counts[name.strip()] = counts.get(name.strip(), 0) + 1

    def score(name: str) -> tuple:
        mixed = name != name.lower() and name != name.upper()
        return (counts[name], -len(name), mixed)

    return max(counts, key=score)


def resolve_brands(orgs: dict[int, dict], won_counts: dict[int, int] | None = None) -> dict[int, Brand]:
    """Map every organisation id to the Brand it belongs to."""
    won_counts = won_counts or {}
    union = _Union()
    by_name: dict[str, int] = {}
    by_domain: dict[str, int] = {}

    for org_id, org in sorted(orgs.items()):
        union.add(org_id)
        raw_name = org.get("name") or ""
        name_key = "" if is_placeholder(raw_name) else normalise_name(raw_name)
        domain = normalise_domain(org.get("website") or "")
        if name_key:
            union.union(by_name.setdefault(name_key, org_id), org_id)
        if domain:
            union.union(by_domain.setdefault(domain, org_id), org_id)

    clusters: dict[int, Brand] = {}
    for org_id, org in sorted(orgs.items()):
        root = union.find(org_id)
        brand = clusters.get(root)
        if brand is None:
            brand = clusters[root] = Brand(key=f"pd:{root}", name="")
        brand.org_ids.append(org_id)
        name = (org.get("name") or "").strip()
        if name:
            brand.names.append(name)
        domain = normalise_domain(org.get("website") or "")
        if domain:
            brand.domains.add(domain)
        brand.won_deals += int(won_counts.get(org_id, org.get("won_deals_count") or 0) or 0)

    for brand in clusters.values():
        brand.name = _display_name(brand.names) if brand.names else brand.key
        # A stable key that survives an organisation being added or merged later.
        brand.key = sorted(brand.domains)[0] if brand.domains else f"name:{normalise_name(brand.name)}"

    return {org_id: brand for brand in clusters.values() for org_id in brand.org_ids}
