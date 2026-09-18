"""Clustering Pipedrive organisations into brands.

Cases are taken from the real account, where "Dr Jart" exists twice with the
won history on one record and the live open deal on the other.
"""
from __future__ import annotations

import pytest

from app.brands import normalise_domain, normalise_name, resolve_brands


# -- normalisation ---------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Cetaphil", "cetaphil"),
        ("cetaphil", "cetaphil"),
        ("Cetaphil Ltd.", "cetaphil"),
        ("CETAPHIL", "cetaphil"),
        ("Dr Jart", "drjart"),
        ("Dr. Jart+", "drjart"),
        ("Beauty of Joseon", "beautyofjoseon"),
        ("Kedrion Biopharma S.p.A.", "kedrionbiopharma"),
        ("L'Oréal", "loreal"),
    ],
)
def test_names_normalise_to_the_same_key(raw, expected):
    assert normalise_name(raw) == expected


def test_a_name_that_is_only_a_legal_suffix_is_not_emptied():
    assert normalise_name("The Group") == "thegroup"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("drjart.com", "drjart.com"),
        ("https://www.drjart.com", "drjart.com"),
        ("HTTP://DrJart.com/uk?ref=1", "drjart.com"),
        ("", ""),
        ("not-a-domain", ""),
    ],
)
def test_websites_normalise_to_a_domain(raw, expected):
    assert normalise_domain(raw) == expected


# -- clustering ------------------------------------------------------------
def test_the_dr_jart_case_pools_won_history_across_records():
    """The live bug: history on org 82, the open deal on org 2513."""
    orgs = {
        82: {"id": 82, "name": "Dr Jart", "website": None, "won_deals_count": 2},
        2513: {"id": 2513, "name": "Dr Jart", "website": "drjart.com", "won_deals_count": 0},
    }
    by_org = resolve_brands(orgs)
    assert by_org[82] is by_org[2513]
    brand = by_org[2513]
    assert brand.won_deals == 2
    assert brand.is_existing_client is True     # was reported as New business
    assert brand.is_duplicated is True


def test_case_differences_merge():
    orgs = {
        1637: {"id": 1637, "name": "Cetaphil", "website": None, "won_deals_count": 0},
        1859: {"id": 1859, "name": "cetaphil", "website": "cetaphil.com", "won_deals_count": 0},
    }
    by_org = resolve_brands(orgs)
    assert by_org[1637] is by_org[1859]
    assert by_org[1637].name == "Cetaphil"      # the tidier spelling wins
    assert by_org[1637].is_existing_client is False


def test_records_sharing_a_website_merge_despite_different_names():
    orgs = {
        1: {"id": 1, "name": "Mars Royal Canin", "website": "royalcanin.com", "won_deals_count": 1},
        2: {"id": 2, "name": "Royal Canin", "website": "https://www.royalcanin.com/uk", "won_deals_count": 0},
    }
    by_org = resolve_brands(orgs)
    assert by_org[1] is by_org[2]
    assert by_org[1].won_deals == 1


def test_a_chain_of_matches_collapses_to_one_brand():
    """A shares a name with B, B shares a domain with C - all one brand."""
    orgs = {
        1: {"id": 1, "name": "Acme", "website": None, "won_deals_count": 1},
        2: {"id": 2, "name": "Acme", "website": "acme.com", "won_deals_count": 0},
        3: {"id": 3, "name": "Acme Worldwide", "website": "acme.com", "won_deals_count": 0},
    }
    by_org = resolve_brands(orgs)
    assert by_org[1] is by_org[2] is by_org[3]
    assert sorted(by_org[1].org_ids) == [1, 2, 3]
    assert by_org[1].won_deals == 1


# -- the important negative cases -----------------------------------------
def test_similar_but_distinct_names_stay_apart():
    """Wrongly merging two real brands is worse than missing a duplicate."""
    orgs = {
        1: {"id": 1, "name": "Olymptrade", "website": None, "won_deals_count": 1},
        2: {"id": 2, "name": "Maree/Olymptrade", "website": None, "won_deals_count": 0},
    }
    by_org = resolve_brands(orgs)
    assert by_org[1] is not by_org[2]


def test_unrelated_brands_are_never_merged():
    orgs = {
        1: {"id": 1, "name": "Runway", "website": "runwayml.com", "won_deals_count": 1},
        2: {"id": 2, "name": "Kia", "website": "kia.com", "won_deals_count": 0},
        3: {"id": 3, "name": "Zonevoice", "website": "zonevoice.com", "won_deals_count": 0},
    }
    by_org = resolve_brands(orgs)
    assert len({id(by_org[i]) for i in (1, 2, 3)}) == 3


def test_records_with_no_name_or_website_do_not_all_collapse_together():
    orgs = {
        1: {"id": 1, "name": "", "website": None, "won_deals_count": 0},
        2: {"id": 2, "name": "", "website": None, "won_deals_count": 0},
    }
    by_org = resolve_brands(orgs)
    assert by_org[1] is not by_org[2]


# -- stability -------------------------------------------------------------
def test_the_brand_key_is_stable_when_another_duplicate_appears():
    """The key is what Supabase remembers, so it must not churn."""
    first = resolve_brands({1: {"id": 1, "name": "Dr Jart", "website": "drjart.com", "won_deals_count": 2}})
    later = resolve_brands({
        1: {"id": 1, "name": "Dr Jart", "website": "drjart.com", "won_deals_count": 2},
        9: {"id": 9, "name": "Dr Jart", "website": None, "won_deals_count": 0},
    })
    assert first[1].key == later[1].key == later[9].key


def test_the_key_prefers_a_domain_over_a_name():
    by_org = resolve_brands({1: {"id": 1, "name": "Dr Jart", "website": "drjart.com", "won_deals_count": 0}})
    assert by_org[1].key == "drjart.com"
    by_org = resolve_brands({1: {"id": 1, "name": "Dr Jart", "website": None, "won_deals_count": 0}})
    assert by_org[1].key == "name:drjart"
