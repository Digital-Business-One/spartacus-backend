from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.models.account import GraduationEntry


class AttendanceRecord(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    # status: registered | confirmed | absent | absent_justified
    # status_label: Aguardando | Validado | Não confirmado | Falta Justificada
    id: str
    date: str                                 # "15 de Março"
    date_sort: str                            # "2026-03-15" for sorting
    time: Optional[str] = None                # "14:30" (HH:MM)
    class_id: str
    class_name: str
    modality_name: str
    teacher_name: Optional[str] = None
    status: str
    status_label: str
    validated_by: Optional[str] = None
    validated_by_name: Optional[str] = None
    validated_at: Optional[str] = None
    justification: Optional[str] = None


class JustificationAttachment(BaseModel):
    """File attached to a justification (image or PDF, ≤10MB — B4/B5)."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    url: str
    name: str
    size: int


class Justification(BaseModel):
    """Embedded on the `attendance` doc (Sub-projeto B: justificativa de faltas).

    Status flow: absent -(justify)-> absent_justification_pending
                 -(approve)-> absent_justified
                 -(reject, requires rejectReason)-> absent

    Prior entries (on reenvio after a rejection) are archived, unmodified,
    into `justificationHistory` on the parent `AttendanceRecord`/Firestore
    doc — this same shape, just appended to a list instead of overwritten.
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    type_id: str
    type_name: str                                    # denormalized
    text: str
    attachment: Optional[JustificationAttachment] = None
    submitted_at: str
    submitted_by: str                                  # uid (student/guardian)
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_by_name: Optional[str] = None
    reject_reason: Optional[str] = None


# ── Task B3: POST /attendance/{doc_id}/justify ─────────────────────────────

class JustifyAttendanceRequest(BaseModel):
    """Body for `POST /attendance/{doc_id}/justify`.

    `type_id` matches a `JustificationType.slug` (see
    `app.models.justification_type`). `attachment` is required only when
    the resolved type has `requiresAttachment=true` — validated at the
    router layer, not here (the type lookup needs a Firestore read).
    `text` is required (design spec: "obrigatório") — blank/whitespace-only
    values are rejected (422), same convention as `CommentCreate.text`
    (see `app.models.comment`).
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    type_id: str
    text: str = Field(min_length=1, max_length=2000)
    attachment: Optional[JustificationAttachment] = None

    @field_validator("text")
    @classmethod
    def _strip_and_reject_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text must not be empty or whitespace-only")
        return stripped


class JustifyAttendanceOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    status: str
    justification: Justification


# ── Task B5: PATCH /attendance/{doc_id}/justification/approve|reject ──────

class RejectJustificationRequest(BaseModel):
    """Body for `PATCH /attendance/{doc_id}/justification/reject`.

    `reason` is required and non-empty (enforced by
    `JustificationService.reject`, which raises 422 on blank/whitespace).
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    reason: str


class MonthSummary(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    month: str           # "2026-03"
    month_label: str     # "Março / 2026"
    attended: int        # 10
    total: int           # 12
    percent: int         # 83
    records: list[AttendanceRecord]


class AttendanceCounts(BaseModel):
    """Counts per attendance category (Frequência Analítica).

    Mirrors the 5 categories computed by
    `attendance_analytics.aggregate_attendance`.
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    confirmed: int = 0
    absent: int = 0
    absent_justified: int = 0
    justification_pending: int = 0
    awaiting_confirmation: int = 0


class GraduationBreakdown(BaseModel):
    """Per belt/degree slice of the same counts + conservative percent.

    `key` is `"{belt}:{degree}"` or `"no_graduation"`. `pending` flags a
    graduation still under review (declared-pending snapshot).
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    key: str
    belt: Optional[str] = None
    degree: Optional[int] = None
    pending: bool = False
    counts: AttendanceCounts
    percent: Optional[float] = None


class AttendanceHistoryOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    streak_days: int     # consecutive training days
    overall_percent: int # weighted average across all months
    months: list[MonthSummary]
    # Frequência Analítica (Task A6) — extended, retrocompatible payload.
    # Counts/percent/byGraduation are computed over the per-turma counting
    # window (max(createdAt, turma.attendanceStartDate); engine-off turmas
    # excluded) and honour the month/modality/belt/degree filters.
    counts: AttendanceCounts = Field(default_factory=AttendanceCounts)
    percent: Optional[float] = None
    by_graduation: list[GraduationBreakdown] = Field(default_factory=list)


# ── RFC-14: Dashboard de Frequência (kanban) ───────────────────────────────

class StudentAttendanceCard(BaseModel):
    """Single person visible in the frequência dashboard columns."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    user_id: str
    name: str
    nickname: Optional[str] = None
    initials: str
    age: Optional[int] = None
    age_category: Optional[str] = None       # "Kids", "Juvenil", "Adulto"...
    photo_url: Optional[str] = None
    roles: list[str] = []
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    guardian_name: Optional[str] = None
    # Belt + degree per modality (same shape as account.graduation)
    graduation: Optional[dict[str, GraduationEntry]] = None
    # Attendance state for today's aula
    # values: "absent" | "registered" | "confirmed"
    status: str = "absent"
    attendance_id: Optional[str] = None
    source: Optional[str] = None             # "qr" | "manual"
    registered_at: Optional[str] = None
    confirmed_at: Optional[str] = None


class ClassBriefOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    id: str
    name: str
    modality_id: str
    modality_name: str
    teacher_name: Optional[str] = None
    start_time: Optional[str] = None         # "19:00"
    end_time: Optional[str] = None           # "20:30"
    total_slots: int = 0
    enrolled_count: int = 0


class AttendanceDashboardOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    class_info: ClassBriefOut = Field(..., alias="class")
    aula_id: Optional[str] = None            # null if no session scheduled today
    date: str                                # "YYYY-MM-DD"
    students: list[StudentAttendanceCard]


class AttendanceActionRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    class_id: str
    user_id: str
    aula_id: Optional[str] = None            # server resolves today's aula if omitted
    source: Optional[str] = "manual"         # "qr" | "manual"
    reason: Optional[str] = None             # for rejections
    # When True, bypass the "aula agendada para hoje" check and create a
    # retroactive attendance record. Restricted by role at the router layer
    # (teacher/instructor/assistant/owner). Used when a class happens off
    # its scheduled day and staff wants to record attendance anyway.
    force: bool = False


class AttendanceActionOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    status: str
    attendance_id: Optional[str] = None


# ── Frequência Analítica (Task A7): analytics agregado por turma ──────────

class AnalyticsStudentOut(BaseModel):
    """One student's roll-up row inside `AttendanceAnalyticsOut.students`.

    `graduation` is the student's CURRENT graduation for the turma's
    modality (resolved fresh from `users/{uid}.graduation`), not the
    historical per-record `graduationSnapshot` used by `/history`.
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    user_id: str
    name: str
    photo_url: Optional[str] = None
    graduation: Optional[GraduationEntry] = None
    counts: AttendanceCounts
    percent: Optional[float] = None
    has_pending_justification: bool = False


class BeltBreakdown(BaseModel):
    """Per-belt slice of `AttendanceAnalyticsOut.students`.

    `key` is `"{belt}:{degree}"` or `"no_graduation"`, same convention as
    `GraduationBreakdown.key`. Unlike `GraduationBreakdown` (per attendance
    record), this counts STUDENTS and averages their individual `percent`.
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    key: str
    belt: Optional[str] = None
    degree: Optional[int] = None
    pending: bool = False
    student_count: int = 0
    average_percent: Optional[float] = None


# ── Task B5: fila de justificativas pendentes no analytics ────────────────

class PendingJustificationOut(BaseModel):
    """One turma record awaiting staff review — the analytics "queue".

    `attendanceId` is the `attendance` doc id, used by the app/backoffice to
    call `PATCH /attendance/{docId}/justification/approve|reject`.
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    attendance_id: str
    user_id: str
    student_name: str
    aula_date: str                                     # aula's timestamp (ISO)
    type_id: str
    type_name: str
    text: str
    attachment: Optional[JustificationAttachment] = None


class AttendanceAnalyticsOut(BaseModel):
    """Staff-only aggregated view of one turma. `GET /attendance/analytics`.

    `students`/`byBelt`/`totals` cover the turma ROSTER (enrolled students),
    not just whoever has a record — an enrolled student with no countable
    record in the period still appears, with `percent=None` and zero
    counts. `averagePercent` is the mean of each student's own `percent`,
    skipping `None` entries so inactive students don't drag it toward 0.
    `totals` is the pooled per-category count across all roster students.
    `pendingJustifications` (Task B5) lists the turma's
    `absent_justification_pending` records within the same window/month —
    the staff review queue — additive to the rest of this payload (A7).
    """

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    average_percent: Optional[float] = None
    totals: AttendanceCounts = Field(default_factory=AttendanceCounts)
    by_belt: list[BeltBreakdown] = []
    students: list[AnalyticsStudentOut] = []
    pending_justifications: list[PendingJustificationOut] = Field(
        default_factory=list,
    )
