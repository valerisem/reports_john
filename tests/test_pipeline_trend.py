"""Rebuilding a past day's pipeline from Pipedrive's change log."""
from __future__ import annotations

import datetime as dt

import pytest

from app.pipeline_trend import (
    is_test_deal, months_before, snapshot_at, snapshot_dates, stage_probabilities, value_at,
)

MOMENT = dt.datetime(2026, 7, 5, 23, 59, 59, tzinfo=dt.timezone.utc)
RATES = {"GBP": 1.0, "USD": 0.75, "EUR": 0.85}
PROBS = {2: 0.45, 7: 0.85}


def deal(**kw):
    base = {"id": 1, "title": "Acme SOW 1", "add_time": "2026-01-01 10:00:00",
            "status": "won", "stage_id": 7, "value": 100_000, "currency": "GBP"}
    base.update(kw)
    return base


def change(field, old, new, when):
    return {"field_key": field, "old_value": old, "new_value": new, "time": when}


# -- the one rule ----------------------------------------------------------
def test_a_field_never_changed_since_keeps_its_current_value():
    assert value_at(deal(), [], "status", MOMENT) == "won"


def test_the_earliest_change_after_the_date_carries_the_value_on_the_day():
    changes = [change("status", "open", "won", "2026-08-01 09:00:00")]
    assert value_at(deal(), changes, "status", MOMENT) == "open"


def test_changes_before_the_date_are_ignored():
    changes = [change("status", "open", "won", "2026-02-01 09:00:00")]
    assert value_at(deal(), changes, "status", MOMENT) == "won"


def test_the_earliest_later_change_wins_not_the_last():
    """A deal reopened and won again still reads 'open' on the day."""
    changes = [
        change("status", "won", "open", "2026-09-01 09:00:00"),
        change("status", "open", "won", "2026-08-01 09:00:00"),
    ]
    assert value_at(deal(), changes, "status", MOMENT) == "open"


# -- snapshots -------------------------------------------------------------
def snap(deals, logs):
    return snapshot_at(label="t", on=dt.date(2026, 7, 5), deals=deals, changelogs=logs,
                       rates=RATES, probability_by_stage=PROBS)


def test_a_deal_open_on_the_day_counts_at_its_value_then():
    d = deal(status="won", value=50_000)
    logs = {1: [change("status", "open", "won", "2026-08-01 09:00:00"),
                change("value", "120000", "50000", "2026-08-02 09:00:00")]}
    result = snap([d], logs)
    assert result.open_deals == 1
    assert result.value_gbp == pytest.approx(120_000)
    assert result.weighted_gbp == pytest.approx(120_000 * 0.85)


def test_a_deal_created_after_the_day_is_not_there_yet():
    assert snap([deal(add_time="2026-09-01 10:00:00", status="open")], {}).open_deals == 0


def test_a_deal_already_won_on_the_day_is_not_open_pipeline():
    assert snap([deal(status="won")], {}).open_deals == 0


def test_a_deal_deleted_later_still_counted_while_it_was_open():
    """Deleted in September, but it was live pipeline in July."""
    d = deal(status="deleted")
    logs = {1: [change("status", "open", "deleted", "2026-09-10 09:00:00")]}
    assert snap([d], logs).open_deals == 1


def test_the_currency_on_the_day_is_the_one_applied():
    d = deal(status="open", currency="GBP", value=100_000)
    logs = {1: [change("currency", "USD", "GBP", "2026-08-01 09:00:00")]}
    assert snap([d], logs).value_gbp == pytest.approx(100_000 * 0.75)


def test_the_stage_on_the_day_sets_the_weighting():
    d = deal(status="open", stage_id=7)
    logs = {1: [change("stage_id", "2", "7", "2026-08-01 09:00:00")]}
    assert snap([d], logs).weighted_gbp == pytest.approx(100_000 * 0.45)


def test_test_deals_are_left_out():
    assert snap([deal(title="test", status="open")], {}).open_deals == 0
    assert is_test_deal("  TestValeria ") is True


def test_an_unknown_stage_weights_at_nothing_rather_than_guessing():
    d = deal(status="open", stage_id=999)
    assert snap([d], {}).weighted_gbp == 0
    assert snap([d], {}).value_gbp == pytest.approx(100_000)


# -- dates -----------------------------------------------------------------
def test_snapshot_dates_are_one_year_six_and_three_months_back():
    labels = snapshot_dates(dt.date(2026, 10, 5))
    assert [str(on) for _, on in labels] == ["2025-10-05", "2026-04-05", "2026-07-05"]
    assert labels[0][0] == "1 year ago"


def test_month_arithmetic_clamps_to_a_real_date():
    assert months_before(dt.date(2026, 3, 31), 1) == dt.date(2026, 2, 28)
    assert months_before(dt.date(2026, 1, 15), 12) == dt.date(2025, 1, 15)


def test_stage_probabilities_are_shares_not_percentages():
    assert stage_probabilities([{"id": 7, "deal_probability": 85}, {"id": None}]) == {7: 0.85}
