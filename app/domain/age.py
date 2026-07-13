"""Pure age helper. birthDate is stored as DD/MM/YYYY (see auth_service)."""
from datetime import date, datetime

ADULT_AGE = 18


def is_adult(birth_date: str | None) -> bool:
    """True iff the person is >= 18. Missing/invalid birthDate → False
    (fail-safe: unknown age is never treated as adult / mentionable)."""
    if not birth_date:
        return False
    try:
        b = datetime.strptime(birth_date, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return False
    today = date.today()
    age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return age >= ADULT_AGE
