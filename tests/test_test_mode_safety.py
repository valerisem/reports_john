"""A test send must never mark a brand as already seen by John.

Recording in test mode would burn brands before they had appeared in a real
email, so they would never be announced. This is the guard on that.
"""
from __future__ import annotations

from datetime import date

import pytest

from app import report as report_module
from app.config import Settings
from app.model import build_report
from tests import fixture


class _SpyStore:
    """Fails loudly if anything tries to write while in test mode."""

    instantiated = False

    def __init__(self, *a, **k):
        type(self).instantiated = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, rows, on):
        raise AssertionError("a test send must not record brands")


@pytest.fixture
def data():
    return build_report(**fixture.load())


@pytest.fixture(autouse=True)
def spy(monkeypatch):
    _SpyStore.instantiated = False
    monkeypatch.setattr(report_module.brand_history, "BrandHistoryStore", _SpyStore)
    return _SpyStore


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://example.supabase.co",
        supabase_key="key",
        mail_from="me@example.com",
        _env_file=None,
    )
    base.update(over)
    return Settings(**base)


def test_a_test_send_records_nothing(data, spy):
    assert report_module._record_brands(_settings(test_mode=True), data) is None
    assert spy.instantiated is False, "the store should not even be opened in test mode"


def test_a_live_send_does_record(data, spy):
    recorded = {}

    class Recording(_SpyStore):
        def record(self, rows, on):
            recorded["rows"] = rows
            recorded["on"] = on
            return len(rows)

    import app.brand_history as bh

    original = bh.BrandHistoryStore
    bh.BrandHistoryStore = Recording
    report_module.brand_history.BrandHistoryStore = Recording
    try:
        count = report_module._record_brands(_settings(test_mode=False), data)
    finally:
        bh.BrandHistoryStore = original

    assert count == len(data.brands)
    assert recorded["rows"], "a live send records every brand in the report"
    assert all(r["brand_key"] for r in recorded["rows"])


def test_recording_failure_never_breaks_the_send(data):
    """The email has already gone; a bookkeeping error must not raise."""

    class Broken(_SpyStore):
        def record(self, rows, on):
            raise RuntimeError("supabase down")

    report_module.brand_history.BrandHistoryStore = Broken
    assert report_module._record_brands(_settings(test_mode=False), data) is None


def test_test_mode_is_the_default():
    """Nothing can reach John, or be marked as seen, until this is turned off."""
    assert Settings(_env_file=None).test_mode is True
