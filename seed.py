"""Sample leads, so the pipeline is not empty when someone opens the demo.
They run through the real engine; only the AI is replaced by written answers (marked 'sample' in the chat)."""
from datetime import timedelta

from engine import handle_message, run_follow_ups, start_lead


def scripted(answers):
    answers = list(answers)

    def ai(config, history, details, missing):
        details, reply = answers.pop(0)
        return {"details": details, "wants_human": False, "reply": reply}, "sample answer"
    return ai


REAL_ESTATE = [
    # (phone, source, hours ago, prefill, [(customer message, details found, reply)])
    ("+971501000101", "WhatsApp", 5, {}, [
        ("Hi, I want to buy a 2-bed apartment in Dubai Marina", {"intent": "buy", "area": "Dubai Marina", "property_type": "2-bed apartment"},
         "Great choice! We have a 2-bed with sea view in Dubai Marina for AED 2,450,000. What is your budget?"),
        ("Up to 2.6M. I'm paying cash and want to move this month. I'm Ahmed",
         {"budget_aed": "2600000", "payment": "cash", "timeline": "now", "name": "Ahmed"}, "Thank you, Ahmed!"),
        ("1", {}, ""),
    ]),
    ("+971502000202", "WhatsApp", 3, {}, [
        ("مرحبا، أبحث عن شقة غرفة واحدة للإيجار في JBR", {"intent": "rent", "area": "JBR", "property_type": "1-bed apartment"},
         "أهلاً بك! لدينا شقة مفروشة بغرفة نوم واحدة في JBR بإيجار 130,000 درهم سنوياً. ما هي ميزانيتك السنوية؟"),
        ("حوالي 140 ألف، وأريد الانتقال خلال شهرين", {"budget_aed": "140000", "timeline": "1-3 months"}, "شكراً لك!"),
    ]),
    ("+971503000303", "Property portal", 26, {"intent": "buy", "area": "Downtown Dubai", "property_type": "3-bed apartment", "name": "Daniel K."}, [
        ("Is the 3-bed in Downtown still available? (listing DR-103)", {},
         "Yes, the 3-bed with Burj view (DR-103) is available at AED 4,800,000. What is your budget?"),
        ("Around 4.5 to 5M. Probably in 4 or 5 months, I still need to arrange a mortgage",
         {"budget_aed": "5000000", "timeline": "3-6 months", "payment": "mortgage not yet"}, "Thank you, Daniel!"),
    ]),
    ("+971504000404", "Website chat", 50, {}, [
        ("Just looking at prices. What do you have under 500k?", {"intent": "buy", "budget_aed": "500000"},
         "Our current listings start from AED 2,300,000 for sale. When are you planning to buy?"),
        ("No plans really, just browsing", {"timeline": "just browsing"}, "No problem at all!"),
    ]),
    ("+971505000505", "WhatsApp", 30, {}, [
        ("Hello, are you hiring real estate agents? I'd like to send my CV", {"intent": "other"}, ""),
    ]),
    ("+971506000606", "WhatsApp", 2, {}, [
        ("Is the Palm Jumeirah villa still available? Cash buyer, ready now", {"intent": "buy", "area": "Palm Jumeirah", "property_type": "5-bed villa", "payment": "cash", "timeline": "now"},
         "Yes, the 5-bed signature villa on Palm Jumeirah is available at AED 32,000,000. What is your budget?"),
        ("Up to 35M. Name is Khalid", {"budget_aed": "35000000", "name": "Khalid"}, "Thank you, Khalid!"),
    ]),
    ("+971507000707", "WhatsApp", 8, {}, [
        ("Hi, looking for a villa in Dubai Hills", {"intent": "buy", "area": "Dubai Hills", "property_type": "villa"},
         "We have a 4-bed villa with garden in Dubai Hills for AED 9,200,000. What is your budget?"),
        ("Can I talk to a real person about the service charges?", {}, ""),
    ]),
    ("+971508000808", "WhatsApp", 75, {}, [
        ("Hi, I want to rent a studio in Business Bay", {"intent": "rent", "area": "Business Bay", "property_type": "studio"},
         "We have a studio near the metro in Business Bay for AED 75,000 a year. What is your budget?"),
        ("STOP", {}, ""),
    ]),
]

CARS = [
    ("+971551000101", "WhatsApp", 4, {}, [
        ("Hi, is the Land Cruiser 2022 still available?", {"intent": "buy", "car": "Toyota Land Cruiser 2022"},
         "Yes! The Land Cruiser 2022 with 48,000 km is available at AED 265,000. What is your budget?"),
        ("Up to 270k cash, I want to buy this week. Omar here", {"budget_aed": "270000", "payment": "cash", "timeline": "this week", "name": "Omar"},
         "Thank you, Omar!"),
    ]),
    ("+971552000202", "Website chat", 28, {}, [
        ("Looking for a BMW, maybe in 2 months, with finance", {"intent": "buy", "car": "BMW", "timeline": "1-3 months", "payment": "finance not yet"},
         "We have a BMW X5 2021 at AED 199,000. What is your budget?"),
        ("About 150k", {"budget_aed": "150000"}, "Thank you!"),
    ]),
]


def load_samples(db, config, now):
    script = REAL_ESTATE if config["booking_word"] == "viewing" else CARS
    for phone, source, hours_ago, prefill, turns in script:
        start = now - timedelta(hours=hours_ago)
        lead_id = start_lead(db, config, phone, source, start, prefill)
        ai = scripted([(details, reply) for _, details, reply in turns])
        for i, (text, details, reply) in enumerate(turns):
            handle_message(db, config, lead_id, text, ai, now=start + timedelta(minutes=2 * i))
        db.update_lead(lead_id, first_reply_seconds=None)      # sample chats are not counted in the speed numbers
    run_follow_ups(db, config, now)
