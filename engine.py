"""The engine: the brain of the system.

Every message from a lead, from any channel (WhatsApp, a property-portal form, the website),
comes to handle_message(). Cheap and certain checks run first; the AI runs last:

1. Save the message and write it in the audit log.
2. 'STOP'?                      -> opt the lead out. No more automatic messages.
3. Is an agent already handling the chat? -> the AI stays quiet.
4. Did the lead pick a viewing time (1, 2 or 3)? -> book it and alert the agent.
5. Did the lead ask for a person? -> hand the chat to the right agent.
6. Otherwise ask the AI: pull out the details and write a reply.
7. Score the lead with fixed rules  -> HOT / WARM / COLD.
8. When the tier is decided: HOT = pick the agent, alert them, offer 3 viewing times.
                              WARM = the agent calls within a day.  COLD = nurture with follow-ups.

If the AI fails (down, slow, quota), the lead still gets a polite reply and a person takes over:
the system fails safely, never silently.
"""
import json
import re
import time

from checks import is_opt_out, language_of, slot_choice, wants_human
from followup import free_slots, next_follow_up, reminder_due, slot_text
from routing import pick_agent
from scoring import score_lead, to_number

FINAL_TIERS = ("HOT", "WARM", "COLD", "NOT A LEAD")
STAGE_FOR_TIER = {"QUALIFYING": "Qualifying", "HOT": "Hot: viewing offered", "WARM": "Warm: agent to call",
                  "COLD": "Nurture", "NOT A LEAD": "Not a lead"}


# ---------- small helpers ----------
def missing_fields(config, details):
    """The questions still to ask, in order. Some are only asked when they matter
    (example: cash or mortgage is asked only to buyers, not to renters)."""
    missing = []
    for field in config["fields"]:
        only_when = field.get("ask_when", {})
        if any(details.get(key) not in allowed for key, allowed in only_when.items()):
            continue
        if not details.get(field["key"]):
            missing.append(field["key"])
    return missing


def simple(text):
    return re.sub(r"[^a-z0-9؀-ۿ]+", " ", str(text).lower()).strip()


def clean_details(config, raw):
    """Checks what the AI returned. Choice boxes must be one of the allowed options;
    money must be a real number. Anything else is dropped, never guessed."""
    clean = {}
    for field in config["fields"]:
        value = str(raw.get(field["key"]) or "").strip()
        if not value:
            continue
        if field.get("options"):
            options = sorted(field["options"], key=len, reverse=True)
            value = next((o for o in options if simple(o) == simple(value)), None) or \
                    next((o for o in options if simple(o) in simple(value)), None)
        elif field.get("number"):
            number = to_number(value)
            value = str(int(number)) if number else None
        if value:
            clean[field["key"]] = value
    return clean


def summary(config, details):
    """One line for the agent: 'buy, Dubai Marina, 2-bed apartment, AED 2,500,000, now, cash'."""
    parts = []
    for field in config["fields"]:
        value = details.get(field["key"])
        if value and field["key"] != "name":
            parts.append("AED {:,.0f}".format(float(value)) if field.get("number") else value)
    return ", ".join(parts) or "no details yet"


def send(db, lead, now, text, started, note=""):
    seconds = round(time.perf_counter() - started, 1)
    db.add_message(lead["id"], now, "out", text, note or "Replied in %.1f s" % seconds)
    db.log(lead["id"], now, "message_out", text)
    if lead["first_reply_seconds"] is None:
        db.update_lead(lead["id"], first_reply_seconds=seconds)
    return text


def alert_agent(db, config, lead, now, agent, tier, details, score):
    text = config["messages"]["agent_alert"].format(tier=tier, name=details.get("name") or "(no name yet)",
                                                    phone=lead["phone"], summary=summary(config, details), score=score)
    db.log(lead["id"], now, "agent_alert", "WhatsApp to %s: %s" % (agent, text))


def hand_over(db, config, lead, now, lang, started, reason, reply=None):
    """A person takes over: the agent gets the full details, the AI stops replying in this chat."""
    details = lead["fields"]
    agent = lead["agent"] or pick_agent(details, config, db.open_leads_by_agent(config["client_name"]))
    db.update_lead(lead["id"], stage="Handover", agent=agent)
    db.log(lead["id"], now, "handover", "%s. Chat passed to %s." % (reason, agent))
    alert_agent(db, config, lead, now, agent, "Handover", details, lead["score"])
    text = reply or config["messages"]["handover"][lang].format(agent=agent)
    return send(db, lead, now, text, started)


# ---------- new leads ----------
def start_lead(db, config, phone, source, now, prefill=None):
    """A new enquiry arrives. The same phone number writing again continues the same lead (no duplicates)."""
    lead_id, is_new = db.find_or_create_lead(config["client_name"], phone, source, now)
    if is_new:
        db.log(lead_id, now, "lead_created", "New lead from %s" % source)
        details = {k: v for k, v in (prefill or {}).items() if v}
        if details:
            db.update_lead(lead_id, fields=details)
            db.log(lead_id, now, "details_found", "From the %s form: %s" % (source, json.dumps(details, ensure_ascii=False)))
    else:
        db.log(lead_id, now, "duplicate_merged", "Same phone number wrote again (from %s): continued the same lead" % source)
    return lead_id


# ---------- the main function ----------
def handle_message(db, config, lead_id, text, ai, now=None):
    """Returns the reply that is sent to the lead, or None when an agent is handling the chat."""
    started = time.perf_counter()
    now = now or db.now()
    lead = db.get_lead(lead_id)
    lang = language_of(text)
    msgs = config["messages"]
    first_contact = not any(m["direction"] == "out" for m in db.messages(lead_id))
    db.add_message(lead_id, now, "in", text)
    db.log(lead_id, now, "message_in", text)
    db.update_lead(lead_id, last_inbound_at=now, followups_sent=0, language=lang)

    # 2. stop word
    if is_opt_out(text, config):
        db.update_lead(lead_id, stage="Opted out", offered_slots=[])
        db.log(lead_id, now, "opted_out", "Lead sent a stop word. No more automatic messages.")
        return send(db, lead, now, msgs["opted_out"][lang], started)
    if lead["stage"] == "Opted out":
        lead["stage"] = "Qualifying"
        db.update_lead(lead_id, stage="Qualifying")
        db.log(lead_id, now, "opted_back_in", "Lead wrote again after STOP, so the chat continues.")
    if lead["stage"] == "Closed: no reply":
        lead["stage"] = STAGE_FOR_TIER.get(lead["tier"], "Qualifying")
        db.update_lead(lead_id, stage=lead["stage"])
        db.log(lead_id, now, "reopened", "Lead replied after the follow-ups ended, so the lead is open again.")

    # 3. a person is handling this chat
    if lead["stage"] == "Handover":
        db.log(lead_id, now, "ai_quiet", "An agent is handling this chat, so the AI did not reply.")
        return None

    # 4. picking a viewing time
    choice = slot_choice(text)
    if lead["offered_slots"] and choice is not None and choice < len(lead["offered_slots"]):
        slot = lead["offered_slots"][choice]
        agent = lead["agent"]
        if slot in db.taken_slots(config["client_name"], agent):        # someone else booked it a moment ago
            slots = free_slots(now, db.taken_slots(config["client_name"], agent))
            db.update_lead(lead_id, offered_slots=slots)
            db.log(lead_id, now, "slot_taken", "%s was just taken; offered new times" % slot_text(slot))
            return send(db, lead, now, msgs["HOT"][lang].format(agent=agent, booking_word=config["booking_word"],
                        slots="\n".join("%d) %s" % (i + 1, slot_text(s)) for i, s in enumerate(slots))), started)
        db.update_lead(lead_id, stage="Viewing booked", viewing_at=slot, booked_at=now, offered_slots=[], reminder_sent=0)
        db.log(lead_id, now, "booked", "%s with %s on %s" % (config["booking_word"].capitalize(), agent, slot_text(slot)))
        db.log(lead_id, now, "agent_alert", "WhatsApp to %s: %s booked a %s on %s. %s." % (
            agent, lead["fields"].get("name") or lead["phone"], config["booking_word"], slot_text(slot),
            summary(config, lead["fields"])))
        return send(db, lead, now, msgs["booked"][lang].format(agent=agent, slot=slot_text(slot),
                                                               booking_word=config["booking_word"]), started)

    # 5. asked for a person
    if wants_human(text, config):
        return hand_over(db, config, lead, now, lang, started, "Lead asked for a person")

    # 6. the AI reads the message and writes the reply
    details = dict(lead["fields"])
    try:
        answer, model = ai(config, db.messages(lead_id), details, missing_fields(config, details))
    except Exception as error:
        db.log(lead_id, now, "ai_error", str(error)[:300])
        return hand_over(db, config, lead, now, lang, started, "The AI could not answer, so a person takes over",
                         reply=msgs["ai_down"][lang])
    found = clean_details(config, answer.get("details", {}))
    changed = {k: v for k, v in found.items() if details.get(k) != v}
    if changed:
        details.update(changed)
        db.log(lead_id, now, "details_found", json.dumps(changed, ensure_ascii=False))

    # 7. score with fixed rules
    score, tier, reasons = score_lead(details, config)
    db.update_lead(lead_id, fields=details, score=score, tier=tier)
    lead.update(fields=details, score=score)
    if tier != lead["tier"]:
        db.log(lead_id, now, "tier_changed", "%s -> %s (score %d/100)" % (lead["tier"], tier, score))

    reply = (answer.get("reply") or "").strip()
    if answer.get("wants_human"):
        return hand_over(db, config, lead, now, lang, started, "The AI saw this needs a person")

    # 8. act on the tier (only when it changes, so the lead never gets the same offer twice)
    if lead["stage"] != "Viewing booked" and tier != lead["tier"]:
        stage = STAGE_FOR_TIER[tier]
        agent = lead["agent"]
        if tier in ("HOT", "WARM") and not agent:
            agent = pick_agent(details, config, db.open_leads_by_agent(config["client_name"]))
            db.log(lead_id, now, "agent_assigned", "%s (by %s)" % (agent, config["routing"]["by_field"]))
        changes = dict(stage=stage, agent=agent)
        if tier == "HOT":
            slots = free_slots(now, db.taken_slots(config["client_name"], agent))
            changes["offered_slots"] = slots
            reply += "\n\n" + msgs["HOT"][lang].format(
                agent=agent, booking_word=config["booking_word"],
                slots="\n".join("%d) %s" % (i + 1, slot_text(s)) for i, s in enumerate(slots)))
            alert_agent(db, config, lead, now, agent, "HOT", details, score)
        elif tier == "WARM":
            reply += "\n\n" + msgs["WARM"][lang].format(agent=agent)
            alert_agent(db, config, lead, now, agent, "WARM", details, score)
        elif tier in ("COLD", "NOT A LEAD"):
            reply = msgs[tier][lang] if tier == "NOT A LEAD" else reply + "\n\n" + msgs["COLD"][lang]
        db.update_lead(lead_id, **changes)
        if stage != lead["stage"]:
            db.log(lead_id, now, "stage_changed", "%s -> %s" % (lead["stage"], stage))

    if first_contact:
        reply = msgs["welcome"][lang] + "\n\n" + reply       # says it is an AI and how to stop messages
    return send(db, lead, now, reply.strip(), started, "Replied in %.1f s by AI (%s)" % (time.perf_counter() - started, model))


# ---------- follow-ups and reminders (run every few minutes in production; here by the demo clock) ----------
def run_follow_ups(db, config, now):
    """Sends every follow-up and reminder that is due by 'now'. Returns how many were sent."""
    sent = 0
    hours = config["follow_up"]["hours_after_last_reply"]
    for row in db.leads(config["client_name"]):
        while True:
            lead = db.get_lead(row["id"])
            due = next_follow_up(lead, now, config)
            if not due:
                break
            number, at = due
            name = lead["fields"].get("name")
            text = config["follow_up"]["texts"][lead["language"]][number].format(name_part=(" " + name) if name else "")
            db.add_message(lead["id"], at, "out", text, "Automatic follow-up %d of %d" % (number + 1, len(hours)))
            db.log(lead["id"], at, "follow_up_sent", "Follow-up %d of %d: %s" % (number + 1, len(hours), text))
            changes = dict(followups_sent=number + 1)
            if number + 1 == len(hours):
                changes.update(stage="Closed: no reply", offered_slots=[])
                db.log(lead["id"], at, "stage_changed", "%s -> Closed: no reply (all follow-ups sent)" % lead["stage"])
            db.update_lead(lead["id"], **changes)
            sent += 1
        lead = db.get_lead(row["id"])
        due = reminder_due(lead, now)
        if due:
            text = config["messages"]["reminder"][lead["language"]].format(
                booking_word=config["booking_word"], agent=lead["agent"], slot=slot_text(lead["viewing_at"]))
            db.add_message(lead["id"], due, "out", text, "Automatic reminder (day before)")
            db.log(lead["id"], due, "reminder_sent", text)
            db.update_lead(lead["id"], reminder_sent=1)
            sent += 1
    return sent
