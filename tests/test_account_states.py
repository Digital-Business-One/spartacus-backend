"""Unit tests for the account state machine (pure domain logic).

Email verification is NOT part of the state machine — it is an auth concern.
All accounts start at PENDING_APPROVAL regardless of login method.
"""

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

    def test_guardian_no_anamnese(self):
        assert resolve_role_group(["guardian"]) == RoleGroup.NO_ANAMNESE

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


# ── Single-approval flow ──────────────────────────────────────────────────────


class TestSingleApproval:
    def test_pending_approval_student_can_approve_directly(self):
        actions = get_available_actions(S.PENDING_APPROVAL, ["student"], "team")
        names = {a.action for a in actions}
        assert "approve" in names
        assert "approve_to_medical" not in names
        assert "request_revision" in names
        assert "reject" in names

    def test_pending_approval_teacher_can_approve(self):
        actions = get_available_actions(S.PENDING_APPROVAL, ["teacher"], "team")
        names = {a.action for a in actions}
        assert "approve" in names

    def test_approve_targets_approved_for_student(self):
        t = find_transition(S.PENDING_APPROVAL, "approve", ["student"])
        assert t is not None
        assert t.target == S.APPROVED

    def test_no_approve_to_medical_transition_exists(self):
        t = find_transition(
            S.PENDING_APPROVAL, "approve_to_medical", ["student"]
        )
        assert t is None

    def test_no_approve_medical_transition_exists(self):
        # state still in enum (legacy reads) but no transition out via approve_medical
        assert find_transition(
            S.PENDING_MEDICAL_HISTORY_APPROVAL, "approve_medical", ["student"]
        ) is None

    def test_registration_revision_still_works(self):
        t = find_transition(S.PENDING_APPROVAL, "request_revision", ["student"])
        assert t is not None and t.target == S.WAITING_REGISTRATION_REVIEW

    def test_revised_registration_approve_targets_approved(self):
        t = find_transition(S.REVISED_REGISTRATION, "approve", ["student"])
        assert t is not None and t.target == S.APPROVED


# ── Available actions: no-anamnese track ───────────────────────────────────────


class TestActionsNoAnamnese:
    def test_pending_approval_teacher(self):
        actions = get_available_actions(S.PENDING_APPROVAL, ["teacher"], "team")
        names = {a.action for a in actions}
        assert "approve" in names
        assert "request_revision" in names
        assert "approve_to_medical" not in names

    def test_pending_approval_guardian(self):
        actions = get_available_actions(S.PENDING_APPROVAL, ["guardian"], "team")
        names = {a.action for a in actions}
        assert "approve" in names
        assert "request_revision" in names
        assert "approve_to_medical" not in names

    def test_approved_teacher_actions(self):
        actions = get_available_actions(S.APPROVED, ["teacher"], "team")
        names = {a.action for a in actions}
        assert "request_revision" in names
        assert "archive" in names
        assert "expel" not in names


# ── Guardian-specific flow ───────────────────────────────────────────────────


class TestGuardianFlow:
    def test_guardian_can_be_approved_directly(self):
        assert can_transition(S.PENDING_APPROVAL, S.APPROVED, ["guardian"])

    def test_guardian_cannot_go_to_medical(self):
        assert not can_transition(
            S.PENDING_APPROVAL, S.WAITING_MEDICAL_HISTORY, ["guardian"]
        )

    def test_guardian_student_can_be_approved_directly(self):
        # single-approval: student+guardian now goes directly to APPROVED
        assert can_transition(
            S.PENDING_APPROVAL, S.APPROVED, ["guardian", "student"]
        )
        assert not can_transition(
            S.PENDING_APPROVAL, S.WAITING_MEDICAL_HISTORY, ["guardian", "student"]
        )


# ── Registration review ──────────────────────────────────────────────────────


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
        actions = get_available_actions(
            S.WAITING_REGISTRATION_REVIEW, ["student"], "user"
        )
        assert any(a.action == "submit_revision" for a in actions)

    def test_revised_to_approved(self):
        actions = get_available_actions(S.REVISED_REGISTRATION, ["teacher"], "team")
        assert any(a.action == "approve" for a in actions)

    def test_revised_no_approve_to_medical(self):
        # approve_to_medical is gone; student should get plain approve
        actions = get_available_actions(S.REVISED_REGISTRATION, ["student"], "team")
        names = {a.action for a in actions}
        assert "approve_to_medical" not in names
        assert "approve" in names


# ── find_transition ────────────────────────────────────────────────────────────


class TestFindTransition:
    def test_valid_transition(self):
        t = find_transition(S.PENDING_APPROVAL, "approve", ["teacher"])
        assert t is not None
        assert t.target == S.APPROVED

    def test_invalid_action(self):
        t = find_transition(S.PENDING_APPROVAL, "nonexistent", ["teacher"])
        assert t is None

    def test_student_can_now_approve(self):
        # single-approval: student also gets approve → APPROVED
        t = find_transition(S.PENDING_APPROVAL, "approve", ["student"])
        assert t is not None
        assert t.target == S.APPROVED

    def test_guardian_approve_direct(self):
        t = find_transition(S.PENDING_APPROVAL, "approve", ["guardian"])
        assert t is not None
        assert t.target == S.APPROVED

    def test_guardian_cannot_approve_to_medical(self):
        t = find_transition(S.PENDING_APPROVAL, "approve_to_medical", ["guardian"])
        assert t is None


# ── can_transition ─────────────────────────────────────────────────────────────


class TestCanTransition:
    def test_valid_path(self):
        assert can_transition(S.PENDING_APPROVAL, S.APPROVED, ["teacher"])

    def test_invalid_path(self):
        assert not can_transition(S.PENDING_APPROVAL, S.EXPELLED, ["teacher"])

    def test_student_can_now_be_approved_directly(self):
        # single-approval: no more anamnese gate
        assert can_transition(S.PENDING_APPROVAL, S.APPROVED, ["student"])

    def test_student_cannot_go_to_medical(self):
        # no transition to WAITING_MEDICAL_HISTORY exists anymore
        assert not can_transition(
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


# ── No email confirmation status exists ──────────────────────────────────────


class TestNoEmailStatus:
    def test_waiting_email_not_in_enum(self):
        """Email verification is no longer an account status."""
        values = [s.value for s in AccountStatus]
        assert "waiting_email_confirmation" not in values
