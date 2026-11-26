"""AI quality test: real customer chats are sent to the REAL AI through the real engine,
and plain code checks the result (details found, tier, agent, language, no made-up prices, no rule leaks).

Unit tests (tests/) check our code with a fake AI. This checks the AI itself, the way a client's
system should be checked before it goes live.
"""
import json
import re
import time
from datetime import datetime

from checks import language_of
from db import DUBAI, Database
from engine import handle_message, start_lead
from scoring import to_number

QUESTION_WORDS = {"area": ["area", "where", "location"], "budget_aed": ["budget"], "timeline": ["when"],
                  "payment": ["cash", "mortgage"], "property_type": ["bedroom", "what kind", "type of"],
                  "intent": ["buy or rent", "buy, rent", "rent or buy"]}


def numbers_in(text):
    """Every money-like number in a text: '2,450,000', '9.2 million', 'AED 130k'."""
    found = re.findall(r"\d[\d,]*(?:\.\d+)?\s*(?:million|mn|m|k|مليون|ألف|الف)?\b", (text or "").lower())
    return {n for n in (to_number(x) for x in found) if n and n >= 10_000}


def check(case, config, lead, replies, customer_text):
    """Returns a list of (what was checked, passed, what we saw)."""
    e, fields, results = case["expect"], lead["fields"], []
    for key, want in e.get("fields", {}).items():
        got = fields.get(key, "")
        if isinstance(want, list):
            ok = got in want
        elif want.startswith("~"):
            ok = want[1:] in got.lower()
        else:
            ok = got == want
        results.append(("%s = %s" % (key, want if not isinstance(want, list) else " or ".join(want)), ok, got or "(empty)"))
    if "tier" in e:
        results.append(("tier = %s" % e["tier"], lead["tier"] == e["tier"], lead["tier"]))
    if "not_tier" in e:
        results.append(("tier is not %s" % "/".join(e["not_tier"]), lead["tier"] not in e["not_tier"], lead["tier"]))
    if "stage" in e:
        results.append(("stage = %s" % e["stage"], lead["stage"] == e["stage"], lead["stage"]))
    if "not_stage" in e:
        results.append(("stage is not %s" % "/".join(e["not_stage"]), lead["stage"] not in e["not_stage"], lead["stage"]))
    if "agent" in e:
        results.append(("agent = %s" % e["agent"], lead["agent"] == e["agent"], lead["agent"] or "(none)"))
    if e.get("no_agent"):
        results.append(("no agent's time used", not lead["agent"], lead["agent"] or "(none)"))
    last = replies[-1] if replies else ""
    if "language" in e:
        results.append(("replies in %s" % ("Arabic" if e["language"] == "ar" else "English"),
                        language_of(last) == e["language"], language_of(last)))
    if "reply_has_any" in e:
        ok = any(w.lower() in last.lower() for w in e["reply_has_any"])
        results.append(("reply mentions one of: %s" % ", ".join(e["reply_has_any"]), ok, last[:120]))
    if "reply_has_none" in e:
        bad = [w for w in e["reply_has_none"] if w.lower() in " ".join(replies).lower()]
        results.append(("never says: %s" % ", ".join(e["reply_has_none"]), not bad, ", ".join(bad) or "clean"))
    if e.get("only_real_prices"):
        allowed = {float(x["price_aed"]) for x in config["listings"]} | numbers_in(customer_text)
        made_up = sorted(numbers_in(" ".join(replies)) - allowed)
        results.append(("no made-up prices", not made_up, ", ".join("{:,.0f}".format(n) for n in made_up) or "none"))
    if e.get("no_repeat_questions"):
        repeats = []
        for known, reply in zip(lead["fields_after_turn"], replies):       # what we knew when this reply was written
            questions = " ".join(s for s in re.split(r"(?<=[.!?؟])\s+", reply) if s.strip().endswith(("?", "؟"))).lower()
            repeats += [k for k in known if any(w in questions for w in QUESTION_WORDS.get(k, []))]
        results.append(("never asks for something already given", not repeats, ", ".join(sorted(set(repeats))) or "none"))
    return results


def run_case(case, config, ai):
    db = Database(":memory:")
    now = datetime.now(DUBAI).replace(microsecond=0)
    lead_id = start_lead(db, config, "+971500000%03d" % case["id"], "WhatsApp", now)
    replies, fields_after_turn, started = [], [], time.time()
    for text in case["messages"]:
        reply = handle_message(db, config, lead_id, text, ai)
        replies.append(reply or "")
        fields_after_turn.append(dict(db.get_lead(lead_id)["fields"]))
    lead = db.get_lead(lead_id)
    lead["fields_after_turn"] = fields_after_turn
    model = next((m["note"].split("AI (")[-1].rstrip(")") for m in db.messages(lead_id) if "AI (" in (m["note"] or "")), "")
    errors = [e["detail"] for e in db.events(config["client_name"], lead_id) if e["kind"] == "ai_error"]
    results = check(case, config, lead, replies, " ".join(case["messages"]))
    if errors:
        results.append(("AI answered without errors", False, errors[0][:150]))
    return {"id": case["id"], "title": case["title"], "client": case["client"], "passed": all(r[1] for r in results),
            "checks": [{"check": c, "passed": ok, "saw": str(saw)} for c, ok, saw in results],
            "chat": [{"customer": t, "reply": r} for t, r in zip(case["messages"], replies)],
            "model": model, "seconds": round(time.time() - started, 1)}


def run_all(configs, ai, pause=3, progress=None):
    cases = json.load(open("evals/cases.json", encoding="utf-8"))
    report = []
    for n, case in enumerate(cases):
        if progress:
            progress(n, len(cases), case["title"])
        report.append(run_case(case, configs[case["client"]], ai))
        time.sleep(pause)                       # stay under the free AI quota per minute
    return {"run_at": datetime.now(DUBAI).strftime("%d %b %Y, %I:%M %p Dubai time"),
            "passed": sum(r["passed"] for r in report), "total": len(report), "cases": report}
