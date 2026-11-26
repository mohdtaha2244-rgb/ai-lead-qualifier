"""Tests of the whole engine with a FAKE AI. The fake AI returns answers we choose,
so we can check the system's own decisions (score, agent, booking, stop, handover) every time, for free."""
import json
from datetime import timedelta

import pytest

from db import Database
from engine import handle_message, run_follow_ups, start_lead

CONFIG = json.load(open("clients/real_estate.json", encoding="utf-8"))


class FakeAI:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, config, history, details, missing):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"details": answer.get("details", {}), "wants_human": answer.get("wants_human", False),
                "reply": answer.get("reply", "OK")}, "fake-model"


@pytest.fixture
def db():
    return Database(":memory:")


def new_lead(db, phone="+971501234567"):
    return start_lead(db, CONFIG, phone, "WhatsApp", db.now())


def events(db, lead_id):
    return [e["kind"] for e in db.events(CONFIG["client_name"], lead_id)]


def test_hot_buyer_gets_an_agent_and_three_viewing_times_then_books(db):
    lead_id = new_lead(db)
    ai = FakeAI({"details": {"intent": "buy", "area": "Dubai Marina", "property_type": "2-bed apartment"},
                 "reply": "Lovely! What is your budget?"},
                {"details": {"budget_aed": "2.5M", "timeline": "now", "payment": "cash"}, "reply": "Thank you!"})
    first = handle_message(db, CONFIG, lead_id, "Hi, I want to buy a 2-bed in Dubai Marina", ai)
    assert first.startswith("Hi! I'm the AI assistant")                  # says it is an AI, and how to stop
    assert db.get_lead(lead_id)["tier"] == "QUALIFYING"

    second = handle_message(db, CONFIG, lead_id, "2.5M, cash, I can move now", ai)
    lead = db.get_lead(lead_id)
    assert lead["tier"] == "HOT" and lead["agent"] == "Sara" and len(lead["offered_slots"]) == 3
    assert lead["fields"]["budget_aed"] == "2500000"
    assert "1)" in second and "3)" in second
    assert "agent_alert" in events(db, lead_id)

    third = handle_message(db, CONFIG, lead_id, "2", ai)
    lead = db.get_lead(lead_id)
    assert lead["stage"] == "Viewing booked" and lead["viewing_at"] is not None
    assert third.startswith("Booked!")
    assert ai.calls == 2                                                  # booking needs no AI


def test_stop_ends_all_messages(db):
    lead_id = new_lead(db)
    ai = FakeAI({"details": {"intent": "buy"}, "reply": "What is your budget?"})
    handle_message(db, CONFIG, lead_id, "Hi, I want to buy", ai)
    handle_message(db, CONFIG, lead_id, "STOP", ai)
    assert db.get_lead(lead_id)["stage"] == "Opted out"
    assert run_follow_ups(db, CONFIG, db.now() + timedelta(days=20)) == 0
    assert ai.calls == 1


def test_asking_for_a_person_hands_over_and_the_ai_goes_quiet(db):
    lead_id = new_lead(db)
    ai = FakeAI()
    reply = handle_message(db, CONFIG, lead_id, "Can I speak to someone please?", ai)
    assert "passed your details" in reply
    assert db.get_lead(lead_id)["stage"] == "Handover"
    assert handle_message(db, CONFIG, lead_id, "Hello?", ai) is None
    assert ai.calls == 0


def test_when_the_ai_fails_a_person_takes_over(db):
    lead_id = new_lead(db)
    reply = handle_message(db, CONFIG, lead_id, "Hi", FakeAI(RuntimeError("503 UNAVAILABLE")))
    assert reply == CONFIG["messages"]["ai_down"]["en"]
    assert db.get_lead(lead_id)["stage"] == "Handover"
    assert "ai_error" in events(db, lead_id)


def test_bad_ai_values_are_dropped_not_guessed(db):
    lead_id = new_lead(db)
    ai = FakeAI({"details": {"intent": "Buy", "timeline": "maybe someday?", "budget_aed": "two million"}})
    handle_message(db, CONFIG, lead_id, "Hi", ai)
    assert db.get_lead(lead_id)["fields"] == {"intent": "buy"}


def test_same_phone_twice_is_one_lead(db):
    first = new_lead(db, "+971551112222")
    second = new_lead(db, "+971551112222")
    assert first == second
    assert "duplicate_merged" in events(db, first)


def test_arabic_message_gets_arabic_system_messages(db):
    lead_id = new_lead(db)
    reply = handle_message(db, CONFIG, lead_id, "أريد التحدث مع موظف", FakeAI())
    assert reply == CONFIG["messages"]["handover"]["ar"].format(agent="Daniel")


def test_six_follow_ups_then_stop_and_reopen_when_they_reply(db):
    lead_id = new_lead(db)
    ai = FakeAI({"details": {"intent": "rent"}, "reply": "Which area?"}, {"details": {"area": "JBR"}, "reply": "Budget?"})
    handle_message(db, CONFIG, lead_id, "I want to rent", ai)
    assert run_follow_ups(db, CONFIG, db.now() + timedelta(hours=1, minutes=1)) == 1
    assert run_follow_ups(db, CONFIG, db.now() + timedelta(days=20)) == 5
    assert db.get_lead(lead_id)["stage"] == "Closed: no reply"
    assert run_follow_ups(db, CONFIG, db.now() + timedelta(days=40)) == 0
    handle_message(db, CONFIG, lead_id, "Sorry, I was travelling. JBR please", ai, now=db.now() + timedelta(days=41))
    assert db.get_lead(lead_id)["stage"] == "Qualifying"


def test_two_leads_never_get_the_same_viewing_time(db):
    hot = {"details": {"intent": "buy", "area": "Dubai Marina", "property_type": "flat", "budget_aed": "3000000",
                       "timeline": "now", "payment": "cash", "name": "X"}}
    a, b = new_lead(db, "+971501111111"), new_lead(db, "+971502222222")
    handle_message(db, CONFIG, a, "buy marina 3M cash now", FakeAI(hot))
    handle_message(db, CONFIG, b, "buy marina 3M cash now", FakeAI(hot))
    handle_message(db, CONFIG, a, "1", FakeAI())
    reply = handle_message(db, CONFIG, b, "1", FakeAI())                   # b was offered the same time: now taken
    assert "1)" in reply and db.get_lead(b)["stage"] != "Viewing booked"
    handle_message(db, CONFIG, b, "1", FakeAI())
    assert db.get_lead(a)["viewing_at"] != db.get_lead(b)["viewing_at"]
