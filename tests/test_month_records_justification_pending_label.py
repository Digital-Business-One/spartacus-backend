"""TDD test for the `_build_month_records` label fix (Task B1 add-on).

Sub-projeto A review finding: `GET /attendance/history` month-records
builder had no branch for `absent_justification_pending`, so such records
fell into the `else` branch and were mislabeled as
"registered"/"Aguardando confirmação" (and wrongly counted as `attended`)
in `months[].records[]`.

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
"""

from datetime import date

from app.services.attendance_service import AttendanceService, _ScheduleItem


class TestBuildMonthRecordsJustificationPendingLabel:
    def test_pending_record_gets_correct_status_and_label(self):
        # Wednesday 2026-07-15 -> weekday() == 2
        schedule_items = [
            _ScheduleItem(
                day_code="wed",
                class_id="turma-1",
                class_name="Jiu Kids",
                modality_name="Jiu Jitsu",
                teacher_name="Prof. Ana",
            ),
        ]
        attendance_by_date = {
            date(2026, 7, 15): [{
                "_id": "att-1",
                "status": "absent_justification_pending",
                "turmaId": "turma-1",
                "turmaName": "Jiu Kids",
            }],
        }

        records, attended, expected = AttendanceService._build_month_records(
            year=2026,
            month=7,
            schedule_items=schedule_items,
            attendance_by_date=attendance_by_date,
            today=date(2026, 7, 18),
        )

        rec = next(r for r in records if r.date_sort == "2026-07-15")
        assert rec.status == "absent_justification_pending"
        assert rec.status_label == "Justificativa em análise"
        # Pending justification still counts as an absence for attendance
        # purposes (Sub-projeto A rule) -- must NOT be counted as attended.
        # (`expected` counts every scheduled Wednesday up to `today`, not
        # just this record -- July 2026 has Wednesdays on 1, 8, 15.)
        assert attended == 0
        assert expected == 3
