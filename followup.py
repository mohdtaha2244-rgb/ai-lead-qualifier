"""Follow-ups: when a lead stops replying, the system sends up to 6 short messages over 14 days, then stops.
It also sends a reminder the day before a booked viewing, so fewer people forget to come (fewer no-shows).

Rule: a follow-up is due a set number of hours after the lead's LAST reply.
When the lead replies again, the count starts again from zero.
"""
from datetime import timedelta

# In these stages a person is already handling the lead, or the lead said stop: no automatic follow-ups.
STOP_STAGES = {"Opted out", "Handover", "Not a lead", "Viewing booked"}


def next_follow_up(lead, now, config):
    """Returns (number, due_time) for the follow-up that should be sent now, or None."""
    if lead["stage"] in STOP_STAGES or not lead["last_inbound_at"]:
        return None
    hours = config["follow_up"]["hours_after_last_reply"]
    sent = lead["followups_sent"]
    if sent >= len(hours):
        return None                       # all 6 sent: stop, never spam
    due = lead["last_inbound_at"] + timedelta(hours=hours[sent])
    return (sent, due) if now >= due else None


def reminder_due(lead, now):
    """Returns the reminder's due time (24 hours before the viewing), or None."""
    if lead["stage"] != "Viewing booked" or lead["reminder_sent"] or not lead["viewing_at"]:
        return None
    due = lead["viewing_at"] - timedelta(hours=24)
    booked_early = lead["booked_at"] is not None and lead["booked_at"] < due   # booked today for tomorrow: no reminder needed
    if booked_early and due <= now < lead["viewing_at"]:
        return due
    return None


def free_slots(now, taken, count=3):
    """The next free viewing times for an agent: tomorrow onwards, 11 AM, 2 PM and 5 PM (Dubai time)."""
    slots = []
    for day in range(1, 8):
        for hour in (11, 14, 17):
            slot = (now + timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if slot not in taken:
                slots.append(slot)
            if len(slots) == count:
                return slots
    return slots


def slot_text(slot):
    return slot.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
