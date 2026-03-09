from contextvars import ContextVar
from dataclasses import dataclass, field

ADMIN_ROLES: frozenset[str] = frozenset({"owner", "assistant"})


@dataclass
class AuthContext:
    user_id: str
    user_email: str
    project_id: str
    roles: list[str] = field(default_factory=list)


# Populated by AuthMiddleware after JWT verification.
# Accessible by @require_roles and Service layer without explicit parameter passing.
auth_ctx: ContextVar[AuthContext | None] = ContextVar("auth_ctx", default=None)
