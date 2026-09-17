"""Custom-field resolution.

Regression cover for a live-data bug: the old contains-match had "am" in its
alias list, so Account Manager resolved to Pipedrive's built-in "Product
amount" field and the column filled with product totals. Silently matching the
wrong field is worse than leaving a column blank, so matching is strict.
"""
from __future__ import annotations

import pytest

from app.pipedrive import PipedriveClient

# Field names taken from the real account.
DEAL_FIELDS = [
    {"name": "Product amount", "key": "product_amount"},
    {"name": "Paid media spend", "key": "a" * 40},
    {"name": "Campaign ID", "key": "b" * 40},
]
ORG_FIELDS = [
    {"name": "Industry", "key": "industry"},                 # built-in, always empty here
    {"name": "Wide niche", "key": "c" * 40},                  # the real industry field
    {"name": "Narrow niche", "key": "d" * 40},                # the real sub-industry field
    {"name": "Account Manager", "key": "e" * 40},
]


@pytest.fixture
def client(monkeypatch):
    c = PipedriveClient.__new__(PipedriveClient)
    catalogue = {"/v1/dealFields": DEAL_FIELDS, "/v1/organizationFields": ORG_FIELDS}
    monkeypatch.setattr(
        PipedriveClient, "_get", lambda self, path, params=None: {"data": catalogue[path]}
    )
    return c


def test_account_manager_does_not_match_product_amount(client):
    """The bug: 'am' matched inside 'Product amount'."""
    assert client._field_key("/v1/dealFields", ("account manager", "am")) is None


def test_industry_prefers_the_custom_field_over_the_empty_builtin(client):
    from app.pipedrive import ORG_INDUSTRY_LABELS

    assert client._field_key("/v1/organizationFields", ORG_INDUSTRY_LABELS) == "c" * 40


def test_sub_industry_resolves_to_narrow_niche(client):
    from app.pipedrive import ORG_SUB_INDUSTRY_LABELS

    assert client._field_key("/v1/organizationFields", ORG_SUB_INDUSTRY_LABELS) == "d" * 40


def test_exact_name_match_wins(client):
    assert client._field_key("/v1/organizationFields", ("account manager",)) == "e" * 40


def test_an_explicit_override_short_circuits_lookup(client):
    assert client._field_key("/v1/dealFields", ("anything",), override="f" * 40) == "f" * 40


def test_short_labels_never_fuzzy_match(client):
    """Anything under the length floor must match exactly or not at all."""
    assert client._field_key("/v1/dealFields", ("id",)) is None
    assert client._field_key("/v1/organizationFields", ("wide",)) is None


def test_fuzzy_match_needs_a_whole_word(client):
    # "media spend" appears as a whole phrase inside "Paid media spend".
    assert client._field_key("/v1/dealFields", ("media spend",)) == "a" * 40
    # "ampaign" is a fragment, not a word.
    assert client._field_key("/v1/dealFields", ("ampaign",)) is None


def test_missing_field_returns_none_rather_than_guessing(client):
    assert client._field_key("/v1/dealFields", ("territory",)) is None
