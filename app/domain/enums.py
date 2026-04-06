"""Domain enums for RFC-11 Timeline."""

from enum import StrEnum


class PostType(StrEnum):
    POST = "post"
    EVENT = "event"
    CHAMPIONSHIP = "championship"


class TimelineEntryType(StrEnum):
    POST = "post"
    EVENT = "event"
    CHAMPIONSHIP = "championship"
    ATTENDANCE = "attendance"
    DONATION = "donation"
    ACCOUNT_CREATED = "account_created"


class TimelineVisibility(StrEnum):
    PUBLIC = "public"
    PERSONAL_AND_STAFF = "personal_and_staff"
    STAFF_ONLY = "staff_only"


class TimelineOrigin(StrEnum):
    TIMELINE_WIZARD = "timeline_wizard"
    CALENDAR = "calendar"
    SYSTEM = "system"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    ABSENT = "absent"


STAFF_ROLES: frozenset[str] = frozenset(
    {"owner", "assistant", "teacher", "instructor"}
)
