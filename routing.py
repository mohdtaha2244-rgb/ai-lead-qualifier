"""Routing: decides which sales agent gets a lead.

1. A very big budget goes to the senior (VIP) agent.
2. Otherwise the agent who covers the lead's area (or car brand) gets it.
   If two agents cover it, the one with fewer open leads gets it, so work is shared fairly.
3. If nobody covers it, the default agent gets it.
"""
from scoring import to_number


def covers(agent, value):
    value = (value or "").lower().strip()
    if len(value) < 2:
        return False
    for place in agent["covers"]:
        place = place.lower()
        if place in value or (len(value) >= 3 and value in place):
            return True
    return False


def pick_agent(lead, config, open_leads=None):
    """open_leads: {agent name: number of open leads}. Returns the agent's name."""
    routing = config["routing"]
    open_leads = open_leads or {}
    vip = routing.get("vip")
    if vip:
        budget = to_number(lead.get(vip["field"]))
        if budget is not None and budget >= vip["min"]:
            return vip["agent"]
    matches = [a["name"] for a in routing["agents"] if covers(a, lead.get(routing["by_field"]))]
    if not matches:
        return routing["default_agent"]
    return min(matches, key=lambda name: open_leads.get(name, 0))
