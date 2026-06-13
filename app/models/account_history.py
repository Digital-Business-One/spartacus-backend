"""Account history model — sub-collection users/{uid}/historic/{eventId}.

Multi-tenant via projectId on each doc. Read by the backoffice account
detail screen (RFC-12, aba Histórico).
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

AccountHistoryEventType = Literal[
    "creation",
    "approval",
    "suspension",
    "warning",
    "edit",
    "donation",
    "attendance",
    "account",
    "graduation",
]


class AccountHistoryEntry(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    id: str
    project_id: str
    event_type: AccountHistoryEventType
    event_subtype: Optional[str] = None
    actor_uid: str
    actor_name: str
    actor_roles: list[str] = []
    description: str
    created_at: str  # ISO8601


class AccountHistoryPage(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    items: list[AccountHistoryEntry]
    total: int
    page: int
    page_size: int
    total_pages: int
