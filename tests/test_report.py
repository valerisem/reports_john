"""End-to-end checks against the 129 real deals in the reference workbook."""
from __future__ import annotations

import io

import openpyxl
import pytest

from app.email_html import _leaderboard, _stage_segments, render_email
from app.excel import build_workbook
from app.formatting import bar_percent, compact_gbp, first_name
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
    assert len(data.brands) == 104
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
    assert f">{data.new_brand_count:,}<" in html


def test_email_lists_ten_brands_per_column(html):
    assert "Top 10 clients" in html
    assert "Top 10 new business" in html
    assert html.count("POC:") == 10


def test_email_counts_the_brands_not_shown(html, data):
    assert f"{len(data.brands) - 20} more brands are in the pipeline" in html


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


def test_leaderboard_runs_winner_first(data):
    entries = _leaderboard(data.by_owner(), data.by_pod(), "https://example.test/{name}.png")
    assert [e["rank"] for e in entries] == [1, 2, 3]
    assert entries[0]["name"] == "Valeriia"
    # the tallest column belongs to the winner, and the order matches the bars
    assert entries[0]["height"] == max(e["height"] for e in entries)


def test_pod_splits_into_managers_plus_the_leads_own_share(data):
    entries = _leaderboard(data.by_owner(), data.by_pod(), "")
    pods = {r["owner"]: r for r in data.by_pod() if r["manager"] is None}
    for entry in entries:
        managed = sum(
            r["weighted_gbp"] for r in data.by_pod()
            if r["owner"] == entry["owner"] and r["manager"]
        )
        total = pods[entry["owner"]]["weighted_gbp"]
        # what the lead handles plus what their managers handle is the whole pod
        assert compact_gbp(total - managed) == entry["direct"]
        assert entry["direct_percent"] == round((total - managed) / total * 100)


def test_photos_are_named_after_plain_first_names(data):
    entries = _leaderboard(data.by_owner(), data.by_pod(), "https://example.test/{name}.png")
    assert entries[0]["photo"] == "https://example.test/valeriia.png"
    assert all(e["photo"] for e in entries)
    # a hyphenated first name still resolves to the plain one: emma.png
    managers = [m for e in entries for m in e["managers"]]
    assert managers, "the reference data should place managers inside pods"
    assert all(m["photo"].endswith(".png") for m in managers)
    assert all("-" not in m["photo"].rsplit("/", 1)[-1] for m in managers)
    blank = _leaderboard(data.by_owner(), data.by_pod(), "")
    assert all(e["photo"] == "" for e in blank)


def test_stage_segments_keep_one_order_and_fill_the_bar(data):
    entries = _leaderboard(data.by_owner(), data.by_pod(), "")
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
    assert "self-managed" in html
