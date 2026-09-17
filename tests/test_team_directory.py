"""Pod / account-owner / account-manager resolution.

Shaped after the real Supabase directory: three pods led by Valeriia Mukhai,
Ritchie Boubouli and Carrick Klopper. Account Owner is the pod lead of whoever
owns the deal in Pipedrive; Account Manager comes from account_manager_orgs and
is independent of the pod.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.team_directory import TeamDirectory, build_directory

TODAY = date(2026, 9, 17)

ROLES = [
    {"id": 8, "name": "client director"},
    {"id": 7, "name": "director of new partnerships"},
    {"id": 9, "name": "partnership manager"},
    {"id": 18, "name": "account manager"},
    {"id": 15, "name": "campaign manager"},
]
TEAM = [
    # Pod leads
    {"id": 10, "full_name": "Valeriia Mukhai", "role_id": 8, "pod_id": 10, "pd_id": "23093401", "left_date": None},
    {"id": 9, "full_name": "Ritchie Boubouli", "role_id": 7, "pod_id": 9, "pd_id": "23093412", "left_date": None},
    {"id": 11, "full_name": "Carrick Klopper", "role_id": 9, "pod_id": 11, "pd_id": "23723008", "left_date": None},
    # Valeriia's pod
    {"id": 75, "full_name": "Emma-Leigh Pedder", "role_id": 18, "pod_id": 10, "pd_id": "27339071", "left_date": None},
    {"id": 47, "full_name": "Maggie Parrott", "role_id": 18, "pod_id": 10, "pd_id": "26998302", "left_date": None},
    {"id": 25, "full_name": "Victoria Gomes", "role_id": 18, "pod_id": 10, "pd_id": "25797509", "left_date": "2026-08-11"},
    # Ritchie's pod
    {"id": 37, "full_name": "Levi Hoang", "role_id": 18, "pod_id": 9, "pd_id": "23134596", "left_date": None},
    {"id": 77, "full_name": "Lucia Lopez Moreno", "role_id": 18, "pod_id": 9, "pd_id": "27784010", "left_date": None},
    {"id": 69, "full_name": "Sarineh Garapetian", "role_id": 18, "pod_id": 9, "pd_id": "26008478", "left_date": None},
    {"id": 42, "full_name": "Johandre Matthysen", "role_id": 15, "pod_id": 9, "pd_id": None, "left_date": None},
    # No pod
    {"id": 1, "full_name": "Inigo Rivero", "role_id": 8, "pod_id": None, "pd_id": "23072424", "left_date": None},
]
MANAGER_ORGS = [
    {"pd_org_id": 2251, "team_id": 75, "org_name": "Runway"},
    {"pd_org_id": 1771, "team_id": 47, "org_name": "Amorepacific"},
    {"pd_org_id": 2076, "team_id": 77, "org_name": "Oreate AI"},
    {"pd_org_id": 1986, "team_id": 37, "org_name": "Webull Securities"},
    {"pd_org_id": 9999, "team_id": 25, "org_name": "Handled by a leaver"},
]


@pytest.fixture(scope="module")
def directory() -> TeamDirectory:
    return build_directory(
        team_rows=TEAM, role_rows=ROLES, manager_org_rows=MANAGER_ORGS, on=TODAY
    )


# -- account owner ---------------------------------------------------------
def test_a_deal_owner_reports_under_their_pod_lead(directory):
    # Maggie Parrott owns the deal; it reports under Valeriia Mukhai.
    assert directory.account_owner(26998302, "Maggie") == "Valeriia Mukhai"
    assert directory.account_owner(27339071, "Emma Pedder") == "Valeriia Mukhai"
    assert directory.account_owner(23134596, "Levi Hoang") == "Ritchie Boubouli"


def test_a_pod_lead_reports_under_themselves(directory):
    assert directory.account_owner(23723008, "Carrick") == "Carrick Klopper"
    assert directory.account_owner(23093401, "Valeriia") == "Valeriia Mukhai"


def test_pipedrive_name_casing_is_corrected_from_the_directory(directory):
    """Pipedrive has 'ritchie'; the directory has the proper name."""
    assert directory.account_owner(23093412, "ritchie") == "Ritchie Boubouli"


def test_someone_with_no_pod_keeps_their_own_name(directory):
    assert directory.account_owner(23072424, "Inigo") == "Inigo Rivero"


def test_an_unknown_pipedrive_user_falls_back(directory):
    assert directory.account_owner(999999, "Someone Else") == "Someone Else"
    assert directory.account_owner(None, "Unassigned") == "Unassigned"


# -- account manager -------------------------------------------------------
def test_account_manager_comes_from_the_org_mapping(directory):
    assert directory.account_manager(2251, "-") == "Emma-Leigh Pedder"
    assert directory.account_manager(1771, "-") == "Maggie Parrott"


def test_account_manager_is_independent_of_the_pod(directory):
    """Oreate AI is owned by Carrick's pod but managed by Ritchie's Lucia."""
    assert directory.account_manager(2076, "-") == "Lucia Lopez Moreno"
    assert directory.pod_for_manager("Lucia Lopez Moreno") == "Ritchie Boubouli"


def test_an_unmapped_org_has_no_manager(directory):
    assert directory.account_manager(4242, "-") == "-"
    assert directory.account_manager(None, "-") == "-"


def test_a_leavers_accounts_are_left_blank(directory):
    """Victoria left on 11 Aug; blank beats naming someone who has gone."""
    assert directory.account_manager(9999, "-") == "-"


# -- pods ------------------------------------------------------------------
def test_pods_list_each_lead_with_their_account_managers(directory):
    pods = {p.lead: p.account_managers for p in directory.pods}
    assert pods["Valeriia Mukhai"] == ["Emma-Leigh Pedder", "Maggie Parrott"]
    assert pods["Ritchie Boubouli"] == ["Levi Hoang", "Lucia Lopez Moreno", "Sarineh Garapetian"]


def test_only_account_managers_are_listed_in_a_pod(directory):
    everyone = [m for p in directory.pods for m in p.account_managers]
    assert "Johandre Matthysen" not in everyone  # campaign manager
    assert "Ritchie Boubouli" not in everyone     # the lead themselves


def test_leavers_are_excluded_from_pods(directory):
    everyone = [m for p in directory.pods for m in p.account_managers]
    assert "Victoria Gomes" not in everyone


def test_a_future_leaving_date_still_counts_as_active():
    """Someone leaving next month is still on the account this month."""
    rows = TEAM + [
        {"id": 29, "full_name": "Blake Hoang", "role_id": 18, "pod_id": 11,
         "pd_id": "24000000", "left_date": "2026-11-12"}
    ]
    d = build_directory(team_rows=rows, role_rows=ROLES, manager_org_rows=[], on=TODAY)
    pods = {p.lead: p.account_managers for p in d.pods}
    assert "Blake Hoang" in pods["Carrick Klopper"]


# -- graceful degradation --------------------------------------------------
def test_an_empty_directory_leaves_the_report_on_pipedrive_names():
    empty = TeamDirectory()
    assert empty.loaded is False
    assert empty.account_owner(26998302, "Maggie") == "Maggie"
    assert empty.account_manager(2251, "-") == "-"
