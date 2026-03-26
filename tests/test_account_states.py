"""Unit tests for the account state machine (pure domain logic)."""

from app.domain.account_states import (
    AccountStatus,
    RoleGroup,
    can_transition,
    find_transition,
    get_available_actions,
    resolve_role_group,
)

S = AccountStatus


# ── Role group resolution ──────────────────────────────────────────────────────


class TestResolveRoleGroup:
    def test_student_needs_anamnese(self):
        assert resolve_role_group(["student"]) == RoleGroup.NEEDS_ANAMNESE

    def test_guardian_needs_anamnese(self):
        assert resolve_role_group(["guardian"]) == RoleGroup.NEEDS_ANAMNESE

    def test_student_and_guardian_needs_anamnese(self):
        assert resolve_role_group(["student", "guardian"]) == RoleGroup.NEEDS_ANAMNESE

    def test_teacher_no_anamnese(self):
        assert resolve_role_group(["teacher"]) == RoleGroup.NO_ANAMNESE

    def test_owner_no_anamnese(self):
        assert resolve_role_group(["owner"]) == RoleGroup.NO_ANAMNESE

    def test_mixed_roles_with_student(self):
        assert resolve_role_group(["teacher", "student"]) == RoleGroup.NEEDS_ANAMNESE

    def test_supporter_no_anamnese(self):
        assert resolve_role_group(["supporter"]) == RoleGroup.NO_ANAMNESE


# ── Available actions: student/guardian track ──────────────────────────────────


class TestActionsNeedsAnamnese:
    def test_pending_approval_student(self):
        actions = get_available_actions(S.PENDING_APPROVAL, ["student"], "team")
        names = {a.action for a in actions}
        assert "approve_to_medical" in names
        assert "request_revision" in names
        assert "reject" in names
        assert "approve" not in names  # students can't skip anamnese

    def test_waiting_medical_history_user_actions(self):
        actions = get_available_actions(S.WAITING_MEDICAL_HISTORY, ["student"], "user")
        names = {a.action for a in actions}
        assert "submit_medical_history" in names

    def test_pending_medical_approval_team(self):
        status = S.PENDING_MEDICAL_HISTORY_APPROVAL
        actions = get_available_actions(status, ["student"], "team")
        names = {a.action for a in actions}
        assert "approve_medical" in names
        assert "request_revision" in names

    def test_approved_student_actions(self):
        actions = get_available_actions(S.APPROVED, ["student"], "team")
        names = {a.action for a in actions}
        assert "request_revision" in names
        assert "expel" in names
        assert "archive" in names


# ── Available actions: no-anamnese track ───────────────────────────────────────


class TestActionsNoAnamnese:
    def test_pending_approval_teacher(self):
        actions = get_available_actions(S.PENDING_APPROVAL, ["teacher"], "team")
        names = {a.action for a in actions}
        assert "approve" in names
        assert "request_revision" in names
        assert "approve_to_medical" not in names  # no anamnese for teachers
        assert "reject" not in names  # reject only for anamnese track

    def test_approved_teacher_actions(self):
        actions = get_available_actions(S.APPROVED, ["teacher"], "team")
        names = {a.action for a in actions}
        assert "request_revision" in names
        assert "archive" in names
        assert "expel" not in names  # expel only for anamnese track


# ── Registration review (reachable from many states) ──────────────────────────


class TestRegistrationReview:
    def test_review_from_pending_approval(self):
        actions = get_available_actions(S.PENDING_APPROVAL, ["student"], "team")
        assert any(a.action == "request_revision" for a in actions)

    def test_review_from_approved(self):
        actions = get_available_actions(S.APPROVED, ["teacher"], "team")
        assert any(a.action == "request_revision" for a in actions)

    def test_review_from_expelled(self):
        actions = get_available_actions(S.EXPELLED, ["student"], "team")
        assert any(a.action == "request_revision" for a in actions)

    def test_review_from_archived(self):
        actions = get_available_actions(S.ARCHIVED, ["owner"], "team")
        assert any(a.action == "request_revision" for a in actions)

    def test_review_from_rejected(self):
        actions = get_available_actions(S.REJECTED, ["student"], "team")
        assert any(a.action == "request_revision" for a in actions)

    def test_user_submits_revision(self):
        status = S.WAITING_REGISTRATION_REVIEW
        actions = get_available_actions(status, ["student"], "user")
        assert any(a.action == "submit_revision" for a in actions)

    def test_revised_to_approved(self):
        actions = get_available_actions(S.REVISED_REGISTRATION, ["teacher"], "team")
        assert any(a.action == "approve" for a in actions)

    def test_revised_to_medical(self):
        actions = get_available_actions(S.REVISED_REGISTRATION, ["student"], "team")
        assert any(a.action == "approve_to_medical" for a in actions)


# ── find_transition ────────────────────────────────────────────────────────────


class TestFindTransition:
    def test_valid_transition(self):
        t = find_transition(S.PENDING_APPROVAL, "approve", ["teacher"])
        assert t is not None
        assert t.target == S.APPROVED

    def test_invalid_action(self):
        t = find_transition(S.PENDING_APPROVAL, "nonexistent", ["teacher"])
        assert t is None

    def test_action_wrong_role_group(self):
        # approve (direct) is only for no_anamnese
        t = find_transition(S.PENDING_APPROVAL, "approve", ["student"])
        assert t is None

    def test_system_transition(self):
        t = find_transition(S.WAITING_EMAIL_CONFIRMATION, "confirm_email", ["student"])
        assert t is not None
        assert t.target == S.PENDING_APPROVAL


# ── can_transition ─────────────────────────────────────────────────────────────


class TestCanTransition:
    def test_valid_path(self):
        assert can_transition(S.PENDING_APPROVAL, S.APPROVED, ["teacher"])

    def test_invalid_path(self):
        assert not can_transition(S.PENDING_APPROVAL, S.EXPELLED, ["teacher"])

    def test_student_cannot_skip_anamnese(self):
        assert not can_transition(S.PENDING_APPROVAL, S.APPROVED, ["student"])

    def test_student_goes_to_medical(self):
        assert can_transition(
            S.PENDING_APPROVAL, S.WAITING_MEDICAL_HISTORY, ["student"]
        )


# ── Terminal states ────────────────────────────────────────────────────────────


class TestTerminalStates:
    def test_rejected_only_revision(self):
        actions = get_available_actions(S.REJECTED, ["student"], "team")
        assert len(actions) == 1
        assert actions[0].action == "request_revision"

    def test_expelled_only_revision(self):
        actions = get_available_actions(S.EXPELLED, ["student"], "team")
        assert len(actions) == 1
        assert actions[0].action == "request_revision"

    def test_archived_has_revision_and_reactivate(self):
        actions = get_available_actions(S.ARCHIVED, ["student"], "team")
        names = {a.action for a in actions}
        assert "request_revision" in names
        assert "reactivate" in names
        assert len(actions) == 2


# ── No actions for waiting_email_confirmation (team) ──────────────────────────


class TestEmailConfirmation:
    def test_no_team_actions(self):
        status = S.WAITING_EMAIL_CONFIRMATION
        actions = get_available_actions(status, ["student"], "team")
        assert len(actions) == 0

    def test_system_has_confirm(self):
        status = S.WAITING_EMAIL_CONFIRMATION
        actions = get_available_actions(status, ["student"], "system")
        assert len(actions) == 1
        assert actions[0].action == "confirm_email"
