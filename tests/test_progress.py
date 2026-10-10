"""The weekly progress section: history, cache, win rate and big clients."""
from __future__ import annotations

import datetime as dt

import pytest

from app import progress
from app.brands import Brand
from app.email_html import plain_text_fallback, render_email
from app.model import build_report
from app.pipeline_trend import Tally
from app.weekly_cache import WeekPoint
from tests import fixture

UTC = dt.timezone.utc
RATES = {"GBP": 1.0, "USD": 0.75}
PROBS = {2: 0.5, 7: 0.8}

NEWCO = Brand(key="newco.com", name="NewCo", org_ids=[1])
OLDCO = Brand(key="oldco.com", name="OldCo", org_ids=[2])
BRANDS = {1: NEWCO, 2: OLDCO}


def deal(id_, org, **kw):
    base = {"id": id_, "title": f"Deal {id_}", "org_id": {"value": org}, "status": "open",
            "stage_id": 2, "value": 1000, "currency": "GBP",
            "add_time": "2026-01-01 09:00:00", "update_time": "2026-01-02 09:00:00"}
    base.update(kw)
    return base


def won(id_, org, when, value=1000):
    return {"id": id_, "title": f"Won {id_}", "org_id": org, "status": "won",
            "won_time": when, "value": value, "currency": "GBP"}


# -- weeks -----------------------------------------------------------------
def test_weeks_end_on_the_last_finished_sunday():
    weeks = progress.week_endings(dt.date(2026, 10, 14), 3)  # a Wednesday
    assert weeks == [dt.date(2026, 9, 27), dt.date(2026, 10, 4), dt.date(2026, 10, 11)]


def test_on_a_sunday_that_sunday_is_not_finished_yet():
    assert progress.week_endings(dt.date(2026, 10, 11), 1) == [dt.date(2026, 10, 4)]


# -- new brands ------------------------------------------------------------
def test_a_brand_is_new_until_its_first_win():
    clock = progress.BrandClock(BRANDS, {1: {"won_deals_count": 1}},
                                [won(9, 1, "2026-06-01 12:00:00")])
    before = dt.datetime(2026, 5, 31, tzinfo=UTC)
    after = dt.datetime(2026, 6, 2, tzinfo=UTC)
    assert clock.is_new(deal(1, 1), before) is True
    assert clock.is_new(deal(1, 1), after) is False


def test_a_win_older_than_the_window_makes_a_client_throughout():
    """Pipedrive counts two wins, the window holds none, so one is older."""
    clock = progress.BrandClock(BRANDS, {2: {"won_deals_count": 2}}, [])
    assert clock.is_new(deal(1, 2), dt.datetime(2025, 1, 1, tzinfo=UTC)) is False


def test_a_deal_with_no_organisation_is_new_business():
    clock = progress.BrandClock(BRANDS, {}, [])
    assert clock.is_new(deal(1, None, org_id=None), dt.datetime(2026, 1, 5, tzinfo=UTC)) is True


# -- cache -----------------------------------------------------------------
def test_cached_weeks_are_reused_and_only_edited_deals_are_reread():
    report_date = dt.date(2026, 10, 14)
    weeks = progress.week_endings(report_date, 3)
    cached = {w: WeekPoint(w, Tally(99), Tally(42)) for w in weeks[:2]}
    deals = [
        deal(1, 1, update_time="2026-03-01 09:00:00"),  # untouched since long before
        deal(2, 1, update_time="2026-10-12 09:00:00"),  # edited after the missing week
    ]
    asked: list[list[int]] = []

    def fetch(ids):
        asked.append(sorted(ids))
        return {}

    clock = progress.BrandClock(BRANDS, {}, [])
    points, fresh = progress.build_weeks(
        report_date=report_date, cached=cached, deals=deals, fetch_changelogs=fetch,
        probability_by_stage=PROBS, clock=clock, count=3,
    )
    assert asked == [[2]]
    assert [p.week_ending for p in fresh] == [weeks[2]]
    assert [p.all.deals for p in points] == [99, 99, 2]
    assert points[2].new.weighted == {"GBP": pytest.approx(1000)}  # 2 x 1000 x 0.5


def test_nothing_is_fetched_when_every_week_is_cached():
    report_date = dt.date(2026, 10, 14)
    cached = {w: WeekPoint(w, Tally(), Tally()) for w in progress.week_endings(report_date, 2)}
    _, fresh = progress.build_weeks(
        report_date=report_date, cached=cached, deals=[deal(1, 1)],
        fetch_changelogs=lambda ids: pytest.fail("no change log should be read"),
        probability_by_stage=PROBS, clock=progress.BrandClock(BRANDS, {}, []), count=2,
    )
    assert fresh == []


def test_a_saved_week_round_trips_and_is_valued_at_todays_rates():
    point = WeekPoint(dt.date(2026, 10, 4), Tally(2, {"USD": 1000.0}, {"USD": 500.0}), Tally())
    back = WeekPoint.from_row(point.to_row())
    assert back == point
    assert back.all.value_gbp(RATES) == pytest.approx(750)


def test_the_workbook_trend_reads_the_nearest_finished_week():
    weeks = [WeekPoint(w, Tally(n), Tally())
             for n, w in enumerate(progress.week_endings(dt.date(2026, 10, 14), 60))]
    trend = progress.trend_from_weeks(dt.date(2026, 10, 14), weeks, RATES)
    assert [s.on for s in trend] == [dt.date(2025, 10, 12), dt.date(2026, 4, 12), dt.date(2026, 7, 12)]
    assert trend[0].label == "1 year ago  (w/e 12 Oct 2025)"


# -- win rate and clients ----------------------------------------------------
def _closed(payload, key):
    return progress.closed_deals(payload, key, RATES, BRANDS)


def test_win_rate_is_by_value_over_the_window():
    w = _closed([won(1, 1, "2026-09-01 10:00:00", 3000)], "won_time")
    lost = _closed([{"id": 2, "title": "x", "org_id": 1, "lost_time": "2026-09-10 10:00:00",
                     "value": 1000, "currency": "GBP"},
                    {"id": 3, "title": "y", "org_id": 1, "lost_time": "2025-01-10 10:00:00",
                     "value": 9000, "currency": "GBP"}], "lost_time")
    rate = progress.win_rate(w, lost, dt.date(2026, 10, 11), weeks=13)
    assert rate.rate == pytest.approx(0.75)  # the old loss is outside the window


def test_no_closed_deals_means_no_rate_rather_than_zero():
    assert progress.win_rate([], [], dt.date(2026, 10, 11), weeks=13).rate is None


def test_big_clients_rank_by_last_years_wins_and_show_their_pipeline():
    w = _closed([
        won(1, 2, "2026-08-01 10:00:00", 5000),
        won(2, 2, "2025-08-01 10:00:00", 5000),   # the year before
        won(3, 1, "2026-09-01 10:00:00", 2000),
    ], "won_time")
    clients = progress.big_clients(w, dt.date(2026, 10, 14), {"OldCo": (2, 800.0)}, limit=5)
    assert [c.name for c in clients] == ["OldCo", "NewCo"]
    assert (clients[0].programmes, clients[0].programmes_before) == (1, 1)
    assert clients[0].open_deals == 2 and clients[1].open_deals == 0


# -- the email -------------------------------------------------------------
def _progress():
    report_date = dt.date(2026, 9, 17)
    weeks = progress.week_endings(report_date, 30)
    points = [WeekPoint(w, Tally(10 + i, {"GBP": 1e5}, {"GBP": 5e4}),
                        Tally(5 + i // 3, {"GBP": 4e4}, {"GBP": 2e4 + 1000 * i}))
              for i, w in enumerate(weeks)]
    now = WeekPoint(report_date, Tally(40), Tally(16, {"GBP": 6e4}, {"GBP": 5.5e4}))
    rates = [progress.WinRate(p.week_ending, 0.3, 3e4, 7e4) for p in points[-26:]]
    rates.append(progress.WinRate(report_date, 0.34, 3.4e4, 6.6e4))
    return progress.Progress(
        rates={"GBP": 1.0}, weeks=points, now=now, win_rates=rates, win_rate_weeks=13,
        clients=[progress.BigClient("OldCo", 9e4, 3, 1, 0, 0.0),
                 progress.BigClient("BigCo", 7e4, 2, 2, 1, 2e4)],
        flow=progress.Flow(since=weeks[-2], added=3, added_gbp=3e4, won=1, won_gbp=1e4),
    )


@pytest.fixture(scope="module")
def data_with_progress():
    data = build_report(**fixture.load())
    data.progress = _progress()
    return data


def test_the_section_answers_all_three_questions(data_with_progress):
    html = render_email(data_with_progress, title="T", greeting_name="John", sender_name="V")
    assert "How we&rsquo;re tracking" in html
    for question in ("Is the new-brand pipeline growing?", "Is the win rate holding?",
                     "Are our biggest clients committing to more?"):
        assert question in html
    assert "Growing" in html            # 16 deals now against 14 a week ago
    assert "Improving" in html          # 34% against 30%
    assert "1 of our top 2 clients has another programme in the pipeline" in html
    assert "Nothing in the pipeline" in html
    # 26 history weeks plus today, in each of the three charts
    assert html.count('valign="bottom" style="height:64px;') == 27 * 3


def test_the_section_comes_before_the_brand_lists(data_with_progress):
    html = render_email(data_with_progress, title="T", greeting_name="John", sender_name="V")
    assert html.index("How we&rsquo;re tracking") < html.index("Top brands by weighted value")


def test_without_progress_the_email_is_unchanged():
    data = build_report(**fixture.load())
    assert "How we" not in render_email(data, title="T", greeting_name="John", sender_name="V")


def test_the_plain_text_version_carries_the_verdicts(data_with_progress):
    text = plain_text_fallback(data_with_progress, "John", "V")
    assert "Growing: 16 open deals with new brands" in text
    assert "OldCo: £90k won" in text


# -- wiring ----------------------------------------------------------------
class FakeClient:
    def __init__(self):
        self.changelog_requests = 0

    def deals_for_history(self, pipeline_id):
        return [deal(1, 1, add_time="2026-01-05 09:00:00", update_time="2026-09-01 09:00:00"),
                deal(2, 2, stage_id=7, add_time="2025-03-05 09:00:00",
                     update_time="2025-04-01 09:00:00")]

    def changelogs(self, ids, workers=4):
        self.changelog_requests += len(ids)
        return {i: [] for i in ids}

    def lost_deals(self, since, pipeline_id):
        return [{"id": 3, "title": "Lost", "org_id": 1, "lost_time": "2026-09-20 10:00:00",
                 "value": 1000, "currency": "GBP"}]


def test_collect_progress_builds_the_whole_section(monkeypatch):
    """Errors are swallowed in production, so prove the happy path really runs."""
    from app import report, weekly_cache
    from app.config import Settings

    saved: list = []
    monkeypatch.setattr(weekly_cache, "load_weeks", lambda *a: {})
    monkeypatch.setattr(weekly_cache, "save_weeks", lambda url, key, pts: saved.extend(pts))
    monkeypatch.setattr(report.log, "warning", lambda *a: pytest.fail(f"swallowed: {a}"))

    data = build_report(**fixture.load())
    client = FakeClient()
    result = report._collect_progress(
        Settings(_env_file=None), client, data,
        orgs={2: {"won_deals_count": 1}}, brands_by_org=BRANDS,
        won_payload=[won(4, 2, "2026-08-01 10:00:00", 5000)],
        stages_payload=[{"id": 2, "deal_probability": 50}, {"id": 7, "deal_probability": 80}],
    )
    assert result is not None
    assert len(saved) == progress.HISTORY_WEEKS == len(result.weeks)
    assert client.changelog_requests == 1          # only the deal edited inside the window
    assert result.now.new.deals == 1 and result.now.all.deals == 2
    assert len(result.win_rates) == 27
    assert [c.name for c in result.clients] == ["OldCo"]
    assert result.flow is not None
    assert "How we&rsquo;re tracking" in render_email(
        _with(data, result), title="T", greeting_name="J", sender_name="V")


def _with(data, result):
    data.progress = result
    return data
