"""Pure aggregation for Frequência Analítica (attendance analytics).

No Firestore/I/O: `aggregate_attendance` consumes a list of plain
attendance record dicts (as they would come from `doc.to_dict()`) and
returns computed counts, percent and byGraduation breakdown. Consumed by
the history/analytics endpoints (later tasks), which are responsible for
fetching records, computing `window_start`, and passing `filters`.

See: docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from app.domain.enums import ValidationStatus

NO_GRADUATION_KEY = "no_graduation"

# Snapshot statuses that do NOT contribute to a real graduation group —
# they fall into the "no_graduation" bucket alongside null snapshots.
_NON_GRADUATION_SNAPSHOT_STATUSES = frozenset({"rejected", "absent"})


class Counts(TypedDict):
    confirmed: int
    absent: int
    absentJustified: int
    justificationPending: int
    awaitingConfirmation: int


def _empty_counts() -> Counts:
    return {
        "confirmed": 0,
        "absent": 0,
        "absentJustified": 0,
        "justificationPending": 0,
        "awaitingConfirmation": 0,
    }


def _apply_status(counts: Counts, status: str) -> None:
    if status == ValidationStatus.CONFIRMED:
        counts["confirmed"] += 1
    elif status == ValidationStatus.ABSENT:
        counts["absent"] += 1
    elif status == ValidationStatus.ABSENT_JUSTIFIED:
        counts["absentJustified"] += 1
    elif status == ValidationStatus.ABSENT_JUSTIFICATION_PENDING:
        counts["justificationPending"] += 1
    elif status == ValidationStatus.REGISTERED:
        counts["awaitingConfirmation"] += 1
    # Unknown statuses are ignored — they don't fit any of the 5
    # attendance categories this feature tracks.


def _percent(counts: Counts) -> float | None:
    """Conservative attendance %.

    confirmed / (confirmed + absent + justification_pending).
    `absent_justified` is neutral (excluded). `registered` (awaiting
    confirmation) is excluded. Denominator 0 -> None (never divide by 0).
    """
    denominator = (
        counts["confirmed"] + counts["absent"] + counts["justificationPending"]
    )
    if denominator == 0:
        return None
    return counts["confirmed"] / denominator


def _parse_timestamp(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _matches_filters(
    record: dict[str, Any], filters: dict[str, Any] | None
) -> bool:
    if not filters:
        return True

    month = filters.get("month")
    if month:
        record_dt = _parse_timestamp(record.get("timestamp"))
        if record_dt is None:
            return False
        if f"{record_dt.year:04d}-{record_dt.month:02d}" != month:
            return False

    modality = filters.get("modality")
    if modality is not None and record.get("modalitySlug") != modality:
        return False

    snapshot = record.get("graduationSnapshot")

    belt = filters.get("belt")
    if belt is not None:
        if not snapshot or snapshot.get("belt") != belt:
            return False

    degree = filters.get("degree")
    if degree is not None:
        if not snapshot or snapshot.get("degree") != degree:
            return False

    return True


def graduation_bucket(
    record: dict[str, Any],
) -> tuple[str, str | None, int | None, bool]:
    """Resolve which byGraduation bucket a record belongs to.

    Returns (key, belt, degree, pending). Null snapshot or a snapshot
    whose status is rejected/absent both fall into "no_graduation".
    """
    snapshot = record.get("graduationSnapshot")
    if not snapshot:
        return (NO_GRADUATION_KEY, None, None, False)

    status = snapshot.get("status")
    if status in _NON_GRADUATION_SNAPSHOT_STATUSES:
        return (NO_GRADUATION_KEY, None, None, False)

    belt = snapshot.get("belt")
    degree = snapshot.get("degree")
    pending = status == "pending"
    return (f"{belt}:{degree}", belt, degree, pending)


def aggregate_attendance(
    records: list[dict[str, Any]],
    window_start: str,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate attendance records into counts, percent and byGraduation.

    Pure function — no Firestore/I/O. `records` are plain dicts with keys
    `status`, `timestamp` (ISO string), `modalitySlug` (str|None),
    `graduationSnapshot` ({belt, degree, status}|None).

    Window: records with `timestamp` before `window_start` are excluded
    entirely (window_start is precomputed by the caller). Filters (month,
    modality, belt, degree) are applied after the window.
    """
    window_dt = _parse_timestamp(window_start)

    counts = _empty_counts()
    groups: dict[str, dict[str, Any]] = {}

    for record in records:
        record_dt = _parse_timestamp(record.get("timestamp"))
        if record_dt is None or window_dt is None:
            continue
        if record_dt < window_dt:
            continue
        if not _matches_filters(record, filters):
            continue

        status = record.get("status")
        _apply_status(counts, status)

        key, belt, degree, pending = graduation_bucket(record)
        group = groups.get(key)
        if group is None:
            group = {
                "key": key,
                "belt": belt,
                "degree": degree,
                "pending": pending,
                "counts": _empty_counts(),
            }
            groups[key] = group
        elif pending:
            group["pending"] = True
        _apply_status(group["counts"], status)

    def _sort_key(group: dict[str, Any]) -> tuple[int, str, str]:
        if group["key"] == NO_GRADUATION_KEY:
            return (1, "", "")
        return (0, str(group["belt"]), str(group["degree"]))

    by_graduation = [
        {
            "key": group["key"],
            "belt": group["belt"],
            "degree": group["degree"],
            "pending": group["pending"],
            "counts": group["counts"],
            "percent": _percent(group["counts"]),
        }
        for group in sorted(groups.values(), key=_sort_key)
    ]

    return {
        "counts": counts,
        "percent": _percent(counts),
        "byGraduation": by_graduation,
    }
