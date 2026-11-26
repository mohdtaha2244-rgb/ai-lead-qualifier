"""Unit tests: each small part is tested alone, with no AI and no internet. Run:  python -m pytest"""
import json
from datetime import datetime, timedelta

import pytest

from checks import clean_uae_phone, is_opt_out, language_of, slot_choice, wants_human
from db import DUBAI
from followup import free_slots, next_follow_up, reminder_due
from routing import pick_agent
from scoring import score_lead, to_number

REAL_ESTATE = json.load(open("clients/real_estate.json", encoding="utf-8"))
CARS = json.load(open("clients/car_dealer.json", encoding="utf-8"))
T = datetime(2026, 11, 26, 10, 0, tzinfo=DUBAI)


# ---------- money ----------
@pytest.mark.parametrize("text, number", [
    ("2M", 2_000_000), ("1.5 million", 1_500_000), ("900k", 900_000), ("AED 2,450,000", 2_450_000),
    ("٢ مليون", 2_000_000), ("120000", 120_000), (2500000, 2_500_000), ("5 months", 5),
])
def test_to_number_reads_money(text, number):
    assert to_number(text) == number


@pytest.mark.parametrize("text", ["", None, "no idea", True])
def test_to_number_without_a_number(text):
    assert to_number(text) is None


# ---------- scoring ----------
def test_ready_cash_buyer_is_hot():
    lead = {"intent": "buy", "area": "Dubai Marina", "property_type": "2-bed apartment", "budget_aed": "2500000",
            "timeline": "now", "payment": "cash", "name": "Ahmed"}
    score, tier, reasons = score_lead(lead, REAL_ESTATE)
    assert (score, tier) == (100, "HOT")


def test_renter_is_not_marked_down_for_no_mortgage():
    lead = {"intent": "rent", "area": "JBR", "property_type": "1-bed", "budget_aed": "120000", "timeline": "1-3 months"}
    score, tier, reasons = score_lead(lead, REAL_ESTATE)
    assert tier == "HOT"
    assert not any("cash" in r["reason"] for r in reasons)      # the payment rule is only for buyers


def test_no_budget_means_still_qualifying_even_if_everything_else_is_strong():
    lead = {"intent": "buy", "area": "Downtown", "property_type": "villa", "timeline": "now", "payment": "cash", "name": "Sam"}
    assert score_lead(lead, REAL_ESTATE)[1] == "QUALIFYING"


def test_slow_mortgage_buyer_is_warm():
    lead = {"intent": "buy", "area": "JVC", "property_type": "townhouse", "budget_aed": "1200000",
            "timeline": "3-6 months", "payment": "mortgage not yet"}
    score, tier, _ = score_lead(lead, REAL_ESTATE)
    assert (score, tier) == (55, "WARM")


def test_low_budget_browser_is_cold():
    lead = {"intent": "buy", "budget_aed": "500000", "timeline": "just browsing"}
    assert score_lead(lead, REAL_ESTATE)[1] == "COLD"


def test_job_seeker_is_not_a_lead():
    assert score_lead({"intent": "other"}, REAL_ESTATE)[1] == "NOT A LEAD"


def test_same_engine_scores_a_car_buyer_with_the_car_rules():
    lead = {"intent": "buy", "car": "Land Cruiser", "budget_aed": "270000", "timeline": "this week", "payment": "cash"}
    assert score_lead(lead, CARS)[1] == "HOT"


# ---------- routing ----------
@pytest.mark.parametrize("area, agent", [
    ("Dubai Marina", "Sara"), ("marina", "Sara"), ("Downtown", "Rashid"), ("business bay", "Rashid"),
    ("JVC", "Priya"), ("Al Barsha", "Daniel"), ("", "Daniel"),
])
def test_agent_is_picked_by_area(area, agent):
    assert pick_agent({"area": area, "budget_aed": "2000000"}, REAL_ESTATE) == agent


def test_very_big_budget_goes_to_the_senior_agent():
    assert pick_agent({"area": "Dubai Marina", "budget_aed": "32000000"}, REAL_ESTATE) == "Fatima (senior agent)"


def test_two_agents_for_one_area_share_the_work():
    config = {"routing": {"by_field": "area", "agents": [{"name": "A", "covers": ["Marina"]}, {"name": "B", "covers": ["Marina"]}],
                          "default_agent": "C"}}
    assert pick_agent({"area": "Marina"}, config, {"A": 5, "B": 2}) == "B"


def test_car_leads_are_routed_by_brand():
    assert pick_agent({"car": "BMW X5"}, CARS) == "Mark"
    assert pick_agent({"car": "Toyota Land Cruiser"}, CARS) == "Yousef"


# ---------- small checks ----------
@pytest.mark.parametrize("text, phone", [
    ("050 123 4567", "+971501234567"), ("+971 55 223 9515", "+971552239515"), ("00971501234567", "+971501234567"),
    ("04 123 4567", None), ("12345", None), ("", None),
])
def test_uae_phone_numbers(text, phone):
    assert clean_uae_phone(text) == phone


def test_stop_words():
    assert is_opt_out("STOP", REAL_ESTATE) and is_opt_out(" stop. ", REAL_ESTATE) and is_opt_out("توقف", REAL_ESTATE)
    assert not is_opt_out("please don't stop sending me listings", REAL_ESTATE)


def test_asking_for_a_person():
    assert wants_human("Can I talk to a real person?", REAL_ESTATE)
    assert wants_human("أريد التحدث مع موظف", REAL_ESTATE)
    assert wants_human("I want to talk to an agent", REAL_ESTATE)
    assert not wants_human("Is the Marina flat still available?", REAL_ESTATE)
    assert not wants_human("Are you hiring real estate agents? I'd like to send my CV", REAL_ESTATE)   # bug found in the demo


@pytest.mark.parametrize("text, choice", [("1", 0), ("2)", 1), ("Option 3", 2), ("٢", 1), ("4", None), ("12", None), ("yes", None)])
def test_slot_choice(text, choice):
    assert slot_choice(text) == choice


def test_language():
    assert language_of("مرحبا") == "ar" and language_of("Hello") == "en"


# ---------- follow-ups ----------
def lead_waiting(**changes):
    lead = {"stage": "Qualifying", "last_inbound_at": T, "followups_sent": 0, "viewing_at": None,
            "booked_at": None, "reminder_sent": 0}
    lead.update(changes)
    return lead


def test_first_follow_up_after_one_hour():
    assert next_follow_up(lead_waiting(), T + timedelta(minutes=30), REAL_ESTATE) is None
    assert next_follow_up(lead_waiting(), T + timedelta(hours=1), REAL_ESTATE) == (0, T + timedelta(hours=1))


def test_never_more_than_six_follow_ups():
    assert next_follow_up(lead_waiting(followups_sent=6), T + timedelta(days=30), REAL_ESTATE) is None


@pytest.mark.parametrize("stage", ["Opted out", "Handover", "Not a lead", "Viewing booked"])
def test_no_follow_ups_in_stop_stages(stage):
    assert next_follow_up(lead_waiting(stage=stage), T + timedelta(days=3), REAL_ESTATE) is None


def test_reminder_the_day_before_a_viewing():
    viewing = T + timedelta(days=2)
    lead = lead_waiting(stage="Viewing booked", viewing_at=viewing, booked_at=T)
    assert reminder_due(lead, T + timedelta(hours=12)) is None
    assert reminder_due(lead, T + timedelta(hours=25)) == viewing - timedelta(hours=24)


def test_no_reminder_when_booked_for_tomorrow_a_few_hours_ago():
    viewing = T + timedelta(hours=20)
    lead = lead_waiting(stage="Viewing booked", viewing_at=viewing, booked_at=T)
    assert reminder_due(lead, T + timedelta(hours=1)) is None


def test_free_slots_skip_booked_times():
    first = free_slots(T, set())
    second = free_slots(T, {first[0]})
    assert len(second) == 3 and first[0] not in second and second[0] == first[1]
    assert all(s > T for s in first)
