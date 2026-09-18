"""End-to-end checks against the 129 real deals in the reference workbook."""
from __future__ import annotations

import io

import openpyxl
import pytest

from app.email_html import _leaderboard, _stage_segments, render_email
from app.excel import build_workbook
from app.formatting import bar_percent, compact_gbp, first_name, percent
from app.model import EXISTING, NEW_BUSINESS, build_report
from tests import fixture


@pytest.fixture(scope="module")
def data():
    return build_report(**fixture.load())


@pytest.fixture(scope="module")
def workbook(data):
    return openpyxl.load_workbook(io.BytesIO(build_workbook(data).getvalue()))


# -- the numbers -----------------------------------------------------------
def test_headline_numbers_match_the_reference_report(data):
    assert data.open_deal_count == 129
    # 103, not the reference workbook's 104: "Olymptrade" and
    # "Maree/Olymptrade" share a website and are now one brand.
    assert len(data.brands) == 103
    assert data.new_brand_count == 53
    assert round(data.pipeline_gbp) == 7_430_747
    assert round(data.weighted_gbp) == 4_352_052


def test_stage_totals_match_the_reference_report(data):
    expected = {
        "Understand Need/Problem": (33, 1_586_687),
        "Create Proposal": (21, 1_409_150),
        "Present Proposal": (12, 991_590),
        "Feedback": (28, 1_803_900),
        "Negotiation": (23, 877_540),
        "IO Sent Out": (12, 761_880),
    }
    actual = {row["stage"]: (row["deals"], round(row["value_gbp"])) for row in data.by_stage()}
    assert actual == expected


def test_owner_totals_match_the_reference_report(data):
    expected = {
        "Valeriia Mukhai": (50, 1_918_792.5),
        "Ritchie Boubouli": (40, 1_242_859.15),
        "Carrick Klopper": (39, 1_190_400.0),
    }
    actual = {row["name"]: (row["deals"], row["weighted_gbp"]) for row in data.by_owner()}
    assert actual.keys() == expected.keys()
    for name, (deals, weighted) in expected.items():
        assert actual[name][0] == deals
        assert actual[name][1] == pytest.approx(weighted, abs=0.01)


def test_deals_are_sorted_by_stage_then_value(data):
    keys = [(-d.stage_order, -d.value_gbp) for d in data.deals]
    assert keys == sorted(keys)


def test_every_deal_carries_a_client_status(data):
    assert {d.client_status for d in data.deals} <= {EXISTING, NEW_BUSINESS}
    assert all(d.client_status for d in data.deals)


def test_weighted_value_is_value_times_stage_probability(data):
    for deal in data.deals:
        assert deal.weighted_gbp == pytest.approx(deal.value_gbp * deal.probability)


def test_brand_pipeline_equals_the_sum_of_its_deals(data):
    for brand in data.brands:
        deals = [d for d in data.deals if d.brand == brand.name]
        assert brand.open_deals == len(deals)
        assert brand.pipeline_gbp == pytest.approx(sum(d.value_gbp for d in deals))


# -- formatting ------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        (4_352_052, "£4.35m"),
        (1_803_900, "£1.80m"),
        (991_590, "£992k"),
        (388_500, "£388k"),
        (74_000, "£74k"),
        (450, "£450"),
        (0, "£0"),
    ],
)
def test_compact_gbp_matches_the_reference_email(value, expected):
    assert compact_gbp(value) == expected


def test_stage_bars_reproduce_the_reference_widths():
    # Values and widths taken straight from the supplied email.
    values = [1_586_687, 1_409_150, 991_590, 1_803_900, 877_540, 761_880]
    maximum = max(values)
    assert [bar_percent(v, maximum, 94) for v in values] == [83, 73, 52, 94, 46, 40]


def test_owner_bars_reproduce_the_reference_widths():
    values = [1_918_793, 1_242_859, 1_190_400]
    assert [bar_percent(v, max(values)) for v in values] == [100, 65, 62]


def test_first_name_shortens_owner_labels():
    assert first_name("Valeriia Mukhai") == "Valeriia"
    assert first_name("") == ""


# -- workbook --------------------------------------------------------------
def test_workbook_has_the_template_sheets(workbook):
    assert workbook.sheetnames == ["Summary", "Open Deals", "Brands", "Settings"]


def test_workbook_row_counts_match_the_data(workbook, data):
    assert workbook["Open Deals"].max_row == len(data.deals) + 1
    assert workbook["Brands"].max_row == len(data.brands) + 1


def test_open_deals_headers_match_the_template(workbook):
    headers = [c.value for c in workbook["Open Deals"][1]]
    assert headers == [
        "Brand", "Deal", "Account Owner", "Account Manager", "Client status", "Stage",
        "Value", "Currency", "Value (£)", "Probability", "Weighted (£)", "Expected close",
        "Days in stage", "Deal added", "Main contact", "Industry", "Sub-industry", "Website",
    ]


def test_brands_headers_match_the_template(workbook):
    headers = [c.value for c in workbook["Brands"][1]]
    assert headers == [
        "Brand", "Client status", "Industry", "Sub-industry", "Website", "Open deals",
        "Pipeline (£)", "Furthest stage", "Account Owner", "Account Manager", "Contacts",
    ]


def test_value_columns_stay_formulas_so_the_sheet_stays_auditable(workbook):
    ws = workbook["Open Deals"]
    assert str(ws["I2"].value).startswith("=")
    assert str(ws["J2"].value).startswith("=")
    assert ws["K2"].value == "=I2*K2".replace("K2", "J2")


def test_summary_kpis_reference_the_full_deal_range(workbook, data):
    last = len(data.deals) + 1
    assert workbook["Summary"]["B5"].value == f"=COUNTA('Open Deals'!$B$2:$B${last})"
    assert workbook["Summary"]["D5"].value == f"=SUM('Open Deals'!$I$2:$I${last})"


def test_settings_sheet_carries_rates_and_stage_probabilities(workbook, data):
    ws = workbook["Settings"]
    assert ws["B3"].value.date() == data.report_date
    assert {ws[f"A{r}"].value for r in (6, 7, 8)} == {"GBP", "USD", "EUR"}
    stage_names = [ws[f"A{11 + i}"].value for i in range(len(data.stages))]
    assert stage_names == [s.name for s in data.stages]


def test_charts_are_attached_to_the_summary(workbook):
    assert len(workbook["Summary"]._charts) == 2


def test_workbook_is_landscape_fit_to_width(workbook):
    for name in workbook.sheetnames:
        assert workbook[name].page_setup.orientation == "landscape"


# -- email -----------------------------------------------------------------
@pytest.fixture(scope="module")
def html(data):
    return render_email(data, title="Sales Pipeline Update",
                        greeting_name="John", sender_name="Valeria")


def test_email_greets_the_recipient_and_signs_off(html):
    assert "Hi John, here is the latest pipeline update." in html
    assert "Valeria" in html


def test_email_shows_the_headline_stats(html, data):
    assert f">{data.open_deal_count:,}<" in html
    assert compact_gbp(data.weighted_gbp) in html
    # The third tile is the new/retained split by value, not a brand count:
    # a raw count of new brands goes stale and repeats week after week.
    assert percent(data.new_business_share) in html
    assert "New business" in html


def test_email_lists_ten_brands_per_column(html, data):
    assert "Top 10 retained clients" in html
    assert "New this week" in html  # wording follows the cadence
    # Without history nothing counts as new, so that column is empty.
    assert data.brands_new_this_report == []
    assert "No new brands since the last update." in html
    # The fallback fills the column so it is never near-empty.
    assert "Top new business in the pipeline" in html
    assert html.count("POC:") == 5


def test_email_counts_the_brands_not_shown(html, data):
    shown = min(10, sum(1 for b in data.brands if b.client_status == EXISTING))
    shown += len(data.brands_new_this_report[:10])
    if len(data.brands_new_this_report) < 3:
        shown += 5      # the fallback list
    assert f"{len(data.brands) - shown} other brands, retained and new are in the pipeline" in html


def test_email_uses_friendly_stage_labels(html):
    for label in ("Discovery", "Preparing proposal", "Proposal presented",
                  "Awaiting feedback", "Negotiating", "Contract sent"):
        assert label in html


def test_test_mode_banner_names_the_real_recipients(data):
    marked = render_email(
        data, title="T", greeting_name="John", sender_name="V",
        test_banner={"to": "john@example.com", "cc": "ops@example.com"},
    )
    assert "TEST MODE" in marked
    assert "john@example.com" in marked and "ops@example.com" in marked
    assert "TEST MODE" not in render_email(data, title="T", greeting_name="John", sender_name="V")


# -- the leaderboard and the stacked stage bars ----------------------------
def test_stage_owner_split_adds_up_to_each_stage_total(data):
    split = data.by_stage_owner()
    for row in data.by_stage():
        if not row["deals"]:
            continue
        assert sum(split[row["stage"]].values()) == pytest.approx(row["value_gbp"])


def test_leaderboard_runs_second_first_third(data):
    entries = _leaderboard(data.by_owner(), "https://example.test/art/{name}.png")
    assert [e["rank"] for e in entries] == [2, 1, 3]
    assert entries[1]["name"] == "Valeriia"
    # the tallest column belongs to the winner
    assert entries[1]["height"] == max(e["height"] for e in entries)


def test_leaderboard_names_each_owner_photo_after_their_first_name(data):
    entries = _leaderboard(data.by_owner(), "https://example.test/art/{name}.png")
    assert entries[1]["photo"] == "https://example.test/art/valeriia.png"
    assert all(e["photo"] for e in entries)
    assert all(e["photo"] == "" for e in _leaderboard(data.by_owner(), ""))


def test_stage_segments_keep_one_order_and_fill_the_bar(data):
    entries = _leaderboard(data.by_owner(), "")
    split = data.by_stage_owner()
    orders = []
    for row in data.by_stage():
        if not row["deals"]:
            continue
        percent = bar_percent(row["value_gbp"], max(r["value_gbp"] for r in data.by_stage()), 94)
        segments = _stage_segments(row["stage"], row["value_gbp"], percent, split, entries)
        assert sum(s["percent"] for s in segments) == percent
        orders.append([s["colour"] for s in segments])
    # every bar stacks the owners in the same order, so the bars compare
    assert len(set(tuple(o) for o in orders)) == 1


def test_email_renders_the_leaderboard_ahead_of_the_stage_chart(data):
    html = render_email(data, title="T", greeting_name="John", sender_name="V")
    assert html.index("Weighted Pipeline Leaderboard") < html.index("Pipeline by stage")
    assert "Weighted by account owner" not in html
    assert "/valeriia.png" in html


# -- brand identity in the report ------------------------------------------
def test_duplicate_organisation_records_collapse_into_one_brand(data):
    """Records sharing a website are one brand, so the figures are not split."""
    names = [b.name for b in data.brands]
    assert len(names) == len(set(names))
    assert "Maree/Olymptrade" not in names
    olymptrade = next(b for b in data.brands if b.name == "Olymptrade")
    assert olymptrade.is_duplicated is True


def test_every_brand_carries_a_stable_key(data):
    keys = [b.brand_key for b in data.brands]
    assert all(keys)
    assert len(keys) == len(set(keys))


def test_nothing_is_flagged_new_without_history(data):
    """A Supabase outage should quieten the email, not re-announce everything."""
    assert data.history.loaded is False
    assert data.brands_new_this_report == []


def test_new_business_share_is_value_based(data):
    assert data.new_business_share == pytest.approx(
        data.new_business_gbp / data.pipeline_gbp
    )
    assert 0.0 < data.new_business_share < 1.0


def test_the_loaded_history_reaches_the_report():
    """Regression: the history was fetched, then dropped on the floor, so
    every brand looked un-announced no matter what Supabase held."""
    from app.brand_history import BrandHistory

    payload = dict(fixture.load())
    known = BrandHistory(reported_keys={"runwayml.com"}, loaded=True)
    built = build_report(**payload, history=known)
    assert built.history.loaded is True
    assert built.history.reported_keys == {"runwayml.com"}


# -- quiet periods ------------------------------------------------------
def _with_history(reported: set[str]):
    from app.brand_history import BrandHistory

    return build_report(**dict(fixture.load()), history=BrandHistory(reported_keys=reported, loaded=True))


def test_a_quiet_period_falls_back_to_the_biggest_new_business():
    """Nothing new: the column still carries the top new business rather than
    showing John an empty box."""
    data = _with_history({b.brand_key for b in build_report(**fixture.load()).brands})
    assert data.brands_new_this_report == []
    html = render_email(data, title="T", greeting_name="John", sender_name="V")
    assert "No new brands since the last update." in html
    assert "Top new business in the pipeline" in html
    assert html.count("POC:") == 5


def test_a_busy_period_shows_only_the_genuinely_new():
    """Three or more new brands is enough on its own; no fallback."""
    data = _with_history(set())
    assert len(data.brands_new_this_report) >= 3
    html = render_email(data, title="T", greeting_name="John", sender_name="V")
    assert "Top new business in the pipeline" not in html


def test_the_fallback_never_repeats_a_brand_already_listed():
    all_brands = build_report(**fixture.load()).brands
    new_business = sorted(
        (b for b in all_brands if b.client_status == NEW_BUSINESS),
        key=lambda b: -b.weighted_gbp,
    )
    # Everything reported except the single biggest new-business brand.
    data = _with_history({b.brand_key for b in all_brands} - {new_business[0].brand_key})
    assert len(data.brands_new_this_report) == 1
    html = render_email(data, title="T", greeting_name="John", sender_name="V")
    assert "Top new business in the pipeline" in html
    assert html.count(f">{new_business[0].name}<") == 1


# -- deals with no organisation linked ------------------------------------
def _payload_with_unlinked_deal(title: str, value: float = 60_000.0) -> dict:
    """The reference payload plus one deal whose org_id is None."""
    payload = fixture.load()
    template = payload["deals_payload"][0]
    payload["deals_payload"] = payload["deals_payload"] + [
        {
            **template,
            "id": 999_999,
            "title": title,
            "org_id": None,
            "person_id": None,
            "value": value,
            "currency": "GBP",
        }
    ]
    return payload


def test_a_deal_with_no_organisation_still_counts(data):
    """Dropping it would silently under-report the stage and pipeline totals."""
    payload = _payload_with_unlinked_deal("Pemberton Tea Rooms x TikTok Campaign")
    report = build_report(**payload)

    assert report.open_deal_count == data.open_deal_count + 1
    assert round(report.pipeline_gbp) == round(data.pipeline_gbp) + 60_000

    kept = report.deals_missing_organisation[0]
    before = next(s for s in data.by_stage() if s["stage"] == kept.stage)
    after = next(s for s in report.by_stage() if s["stage"] == kept.stage)
    assert after["deals"] == before["deals"] + 1


def test_an_unlinked_deal_takes_its_brand_from_the_title():
    report = build_report(**_payload_with_unlinked_deal("Pemberton Tea Rooms x TikTok Campaign"))
    kept = report.deals_missing_organisation
    assert [d.brand for d in kept] == ["Pemberton Tea Rooms"]

    brand = next(b for b in report.brands if b.name == "Pemberton Tea Rooms")
    # No brand key, so it can never be announced to John as new or written to
    # the history table on the strength of a deal title alone.
    assert brand.brand_key == ""
    assert brand.is_new_this_report is False
    assert brand.name not in {b["brand_name"] for b in report.brand_records()}


def test_an_unlinked_deal_rejoins_a_brand_it_names_exactly(data):
    known = data.brands[0].name
    report = build_report(**_payload_with_unlinked_deal(f"{known} x Winter Campaign"))

    assert len(report.brands) == len(data.brands)  # no duplicate brand row
    brand = next(b for b in report.brands if b.name == known)
    assert brand.open_deals == data.brands[0].open_deals + 1
    assert brand.client_status == data.brands[0].client_status
    assert brand.brand_key == data.brands[0].brand_key


def test_an_unlinked_deal_with_no_usable_title_is_dropped(data):
    report = build_report(**_payload_with_unlinked_deal(""))
    assert report.open_deal_count == data.open_deal_count
