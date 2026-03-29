"""Pure domain logic for the account approval state machine (RFC-05).

This module has ZERO IO dependencies — no Firestore, no Firebase, no HTTP.
It defines states, transitions, and pure functions that other layers consume.

Email verification is NOT an account status — it is an auth-layer concern
stored as a field (emailVerified) on the user document.
All accounts start at PENDING_APPROVAL regardless of login method.
"""

from dataclasses import dataclass
from enum import StrEnum

# ── States ─────────────────────────────────────────────────────────────────────


class AccountStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    WAITING_MEDICAL_HISTORY = "waiting_medical_history"
    PENDING_MEDICAL_HISTORY_APPROVAL = "pending_medical_history_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPELLED = "expelled"
    ARCHIVED = "archived"
    WAITING_REGISTRATION_REVIEW = "waiting_registration_review"
    REVISED_REGISTRATION = "revised_registration"


# ── Role groups ────────────────────────────────────────────────────────────────


class RoleGroup(StrEnum):
    NEEDS_ANAMNESE = "needs_anamnese"
    NO_ANAMNESE = "no_anamnese"


_ANAMNESE_ROLES = frozenset({"student"})

BOTH = frozenset({RoleGroup.NEEDS_ANAMNESE, RoleGroup.NO_ANAMNESE})
ANAMNESE_ONLY = frozenset({RoleGroup.NEEDS_ANAMNESE})
NO_ANAMNESE_ONLY = frozenset({RoleGroup.NO_ANAMNESE})


def resolve_role_group(roles: list[str]) -> RoleGroup:
    """Determine which track a user follows based on their roles."""
    if any(r in _ANAMNESE_ROLES for r in roles):
        return RoleGroup.NEEDS_ANAMNESE
    return RoleGroup.NO_ANAMNESE


# ── Transitions ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Transition:
    source: AccountStatus
    target: AccountStatus
    action: str
    label: str
    role_groups: frozenset[RoleGroup]
    executed_by: str  # "team" | "user"


S = AccountStatus  # shorthand

TRANSITIONS: list[Transition] = [
    # ── Team transitions: pending_approval ────────────────────────────────
    Transition(
        S.PENDING_APPROVAL, S.WAITING_MEDICAL_HISTORY,
        "approve_to_medical", "Enc. anamnese",
        ANAMNESE_ONLY, "team",
    ),
    Transition(
        S.PENDING_APPROVAL, S.APPROVED,
        "approve", "Aprovar conta", NO_ANAMNESE_ONLY, "team",
    ),
    Transition(
        S.PENDING_APPROVAL, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar revisão cadastral", BOTH, "team",
    ),
    Transition(
        S.PENDING_APPROVAL, S.REJECTED,
        "reject", "Rejeitar cadastro", ANAMNESE_ONLY, "team",
    ),
    # ── User transitions: medical history ─────────────────────────────────
    Transition(
        S.WAITING_MEDICAL_HISTORY, S.PENDING_MEDICAL_HISTORY_APPROVAL,
        "submit_medical_history", "Enviar anamnese",
        ANAMNESE_ONLY, "user",
    ),
    Transition(
        S.WAITING_MEDICAL_HISTORY, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar revisão cadastral",
        ANAMNESE_ONLY, "team",
    ),
    # ── Team transitions: medical history approval ────────────────────────
    Transition(
        S.PENDING_MEDICAL_HISTORY_APPROVAL, S.APPROVED,
        "approve_medical", "Aprovar anamnese",
        ANAMNESE_ONLY, "team",
    ),
    Transition(
        S.PENDING_MEDICAL_HISTORY_APPROVAL, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar revisão cadastral",
        ANAMNESE_ONLY, "team",
    ),
    # ── Team transitions: approved ────────────────────────────────────────
    Transition(
        S.APPROVED, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar revisão cadastral", BOTH, "team",
    ),
    Transition(
        S.APPROVED, S.EXPELLED,
        "expel", "Expulsar", ANAMNESE_ONLY, "team",
    ),
    Transition(
        S.APPROVED, S.ARCHIVED,
        "archive", "Arquivar", BOTH, "team",
    ),
    # ── Team transitions: terminal states ─────────────────────────────────
    Transition(
        S.EXPELLED, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar revisão",
        ANAMNESE_ONLY, "team",
    ),
    Transition(
        S.ARCHIVED, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar revisão", BOTH, "team",
    ),
    Transition(
        S.ARCHIVED, S.APPROVED,
        "reactivate", "Reativar conta", BOTH, "team",
    ),
    Transition(
        S.REJECTED, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar revisão",
        ANAMNESE_ONLY, "team",
    ),
    # ── User transitions: registration review ─────────────────────────────
    Transition(
        S.WAITING_REGISTRATION_REVIEW, S.REVISED_REGISTRATION,
        "submit_revision", "Enviar revisão", BOTH, "user",
    ),
    # ── Team transitions: revised registration ────────────────────────────
    Transition(
        S.REVISED_REGISTRATION, S.WAITING_REGISTRATION_REVIEW,
        "request_revision", "Solicitar nova revisão", BOTH, "team",
    ),
    Transition(
        S.REVISED_REGISTRATION, S.WAITING_MEDICAL_HISTORY,
        "approve_to_medical", "Enc. anamnese",
        ANAMNESE_ONLY, "team",
    ),
    Transition(
        S.REVISED_REGISTRATION, S.APPROVED,
        "approve", "Aprovar conta", BOTH, "team",
    ),
]


# ── Pure query functions ───────────────────────────────────────────────────────


def get_available_actions(
    current_status: str,
    roles: list[str],
    executed_by_filter: str | None = None,
) -> list[Transition]:
    """Return all valid transitions from *current_status* for *roles*."""
    role_group = resolve_role_group(roles)
    return [
        t for t in TRANSITIONS
        if t.source == current_status
        and role_group in t.role_groups
        and (executed_by_filter is None or t.executed_by == executed_by_filter)
    ]


def find_transition(
    current_status: str,
    action: str,
    roles: list[str],
) -> Transition | None:
    """Find a specific transition by action name. None if invalid."""
    role_group = resolve_role_group(roles)
    for t in TRANSITIONS:
        if (
            t.source == current_status
            and t.action == action
            and role_group in t.role_groups
        ):
            return t
    return None


def can_transition(
    current_status: str,
    target_status: str,
    roles: list[str],
) -> bool:
    """Check if moving from *current_status* to *target_status* is valid."""
    role_group = resolve_role_group(roles)
    return any(
        t.source == current_status
        and t.target == target_status
        and role_group in t.role_groups
        for t in TRANSITIONS
    )
