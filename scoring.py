"""Lead scoring: turns what a lead told us into a score from 0 to 100 and a tier (HOT, WARM, COLD).

The rules come from the client's file in clients/, so every business can have its own rules.
There is no AI in this file on purpose: the same answers always give the same score,
and every point has a written reason that a sales manager can read and change.
"""
import re

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def to_number(value):
    """Reads an amount of money. '2M' -> 2000000, '1.5 million' -> 1500000, '900k' -> 900000,
    'AED 2,450,000' -> 2450000. Returns None when there is no number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).lower().translate(ARABIC_DIGITS).replace(",", "").replace("aed", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(million|mn|m|thousand|k|مليون|ألف|الف)?\b", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or ""
    if unit in ("million", "mn", "m", "مليون"):
        number *= 1_000_000
    elif unit in ("thousand", "k", "ألف", "الف"):
        number *= 1_000
    return number


def rule_applies(rule, lead):
    """Some rules only count for some leads. Example: the 'buy' budget rule does not count for renters."""
    for field, allowed in rule.get("when", {}).items():
        if lead.get(field) not in allowed:
            return False
    return True


def rule_passes(rule, lead):
    value = lead.get(rule["field"])
    if rule["check"] == "filled":
        return bool(value)
    if rule["check"] == "in":
        return value in rule["value"]
    if rule["check"] == "min":
        number = to_number(value)
        return number is not None and number >= rule["value"]
    raise ValueError("Unknown check: %s" % rule["check"])


def pick_tier(lead, score, scoring):
    if lead.get("intent") in scoring.get("not_a_lead", []):
        return "NOT A LEAD"
    if any(not lead.get(field) for field in scoring["required"]):
        return "QUALIFYING"            # we never call a lead HOT before we know what, how much and when
    if score >= scoring["hot"]:
        return "HOT"
    if score >= scoring["warm"]:
        return "WARM"
    return "COLD"


def score_lead(lead, config):
    """Returns (score, tier, reasons). Score = points earned / points possible for this lead, as a percent.

    Rules about the same detail are alternatives, so they count once: 'moving within 3 months' (25)
    and 'within 6 months' (10) can never both be true, so that detail is worth 25 at most, not 35."""
    scoring = config["scoring"]
    groups = {}                                   # detail name -> the rules about it that apply to this lead
    for rule in scoring["rules"]:
        if rule_applies(rule, lead):
            groups.setdefault(rule["field"], []).append(rule)
    earned = possible = 0
    reasons = []
    for rules in groups.values():
        best = max(r["points"] for r in rules)
        passed = [r for r in rules if rule_passes(r, lead)]
        got = max(passed, key=lambda r: r["points"]) if passed else max(rules, key=lambda r: r["points"])
        possible += best
        earned += got["points"] if passed else 0
        reasons.append({"reason": got["reason"], "points": got["points"] if passed else 0, "out_of": best})
    score = round(100 * earned / possible) if possible else 0
    return score, pick_tier(lead, score, scoring), reasons
