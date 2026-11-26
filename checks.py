"""Small checks on each message, done with plain code before any AI is used.
Plain code is instant, free and always gives the same answer, so we use it wherever it is enough.
"""
import re

ARABIC_LETTERS = re.compile(r"[؀-ۿ]")


def language_of(text):
    """'ar' if the message has Arabic letters, otherwise 'en'."""
    return "ar" if ARABIC_LETTERS.search(text or "") else "en"


def clean_uae_phone(text):
    """Turns '050 123 4567', '+971501234567' or '00971 50 123 4567' into '+971501234567'.
    Returns None if it is not a UAE mobile number (UAE mobiles start with 50, 52, 54, 55, 56 or 58)."""
    digits = re.sub(r"\D", "", text or "")
    if digits.startswith("00971"):
        digits = digits[5:]
    elif digits.startswith("971"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if re.fullmatch(r"5[024568]\d{7}", digits):
        return "+971" + digits
    return None


def is_opt_out(text, config):
    """True only when the whole message is a stop word, like 'STOP'. 'Don't stop' is not an opt-out."""
    return (text or "").strip().strip(".!").lower() in config["words"]["opt_out"]


def wants_human(text, config):
    lowered = (text or "").lower()
    return any(word in lowered for word in config["words"]["human"])


def slot_choice(text):
    """'1', '2)', 'option 3' or Arabic '٢' -> 0, 1 or 2 (the slot's position). Anything else -> None."""
    text = (text or "").strip().lower().translate(str.maketrans("١٢٣", "123"))
    match = re.fullmatch(r"(?:option\s*)?([123])\s*[).]?", text)
    return int(match.group(1)) - 1 if match else None
