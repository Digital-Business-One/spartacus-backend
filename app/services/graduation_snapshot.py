"""Graduation snapshot + modality slug helpers for attendance creation.

Writes an immutable `graduationSnapshot` + denormalized `modalitySlug` onto
every attendance record **at creation time only**, in the 3 write paths:
`checkin_service.checkin`, `attendance_service.confirm_attendance` (create
branch) and `absence_job_service.compute`. Updates to an existing attendance
record must NEVER re-apply these fields — promoting a student must not alter
the graduation recorded on past attendance.

See: docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
("Graduação — snapshot na presença").
"""

from __future__ import annotations

from typing import Any

_MODALITIES = "modalities"


def _normalize_key(key: str) -> str:
    """Normalize a graduation/modality map key (legacy name -> slug)."""
    return (key or "").strip().lower().replace(" ", "-")


def resolve_modality_slug(
    db: Any, class_data: dict[str, Any] | None,
) -> str | None:
    """Resolve a turma's modality slug via classes.modalityId -> modalities.

    Mirrors the existing modality-*name* resolution already used across
    checkin_service/attendance_service (`_resolve_modality`), but reads the
    modality doc's `slug` field instead of `name`. Returns None when there's
    no `modalityId` on the class or the modality doc doesn't exist/has no
    slug — callers treat that as "denormalize nothing" (spec: nullable).
    """
    if not class_data:
        return None
    modality_id = class_data.get("modalityId", "")
    if not modality_id:
        return None
    mod_doc = db.collection(_MODALITIES).document(modality_id).get()
    if not mod_doc.exists:
        return None
    return mod_doc.to_dict().get("slug") or None


def build_graduation_snapshot(
    user_data: dict[str, Any] | None, modality_slug: str | None,
) -> dict[str, Any] | None:
    """Copy `users/{uid}.graduation[modalitySlug]` into an immutable snapshot.

    Returns None when the user has no `graduation` map, no `modality_slug`
    was resolved, or there's no graduation entry for that modality (student
    never graded in it) — these all mean "no graduation snapshot" per spec.

    The returned dict only has the 3 fields the spec calls out
    (`belt`, `degree`, `status`) — extra fields on the source
    `GraduationEntry` (e.g. `locked_by_student`, `graded_by`) are
    intentionally dropped from the snapshot.
    """
    if not user_data or not modality_slug:
        return None
    graduation = user_data.get("graduation")
    if not isinstance(graduation, dict):
        return None

    normalized = {
        _normalize_key(k): v
        for k, v in graduation.items()
        if isinstance(v, dict)
    }
    entry = normalized.get(_normalize_key(modality_slug))
    if not entry:
        return None

    return {
        "belt": entry.get("belt", ""),
        "degree": entry.get("degree", 0),
        "status": entry.get("status", "approved"),
    }
