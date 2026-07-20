"""TDD tests for the pure attendance aggregation function (Task A3).

`aggregate_attendance` has no I/O: it consumes plain attendance record
dicts (as would come from Firestore `.to_dict()`) and returns computed
counts/percent/byGraduation. See:
docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
"""

from app.services.attendance_analytics import aggregate_attendance

WINDOW = "2026-07-15T00:00:00-04:00"


def _record(
    status: str,
    timestamp: str = "2026-07-16T10:00:00-04:00",
    modality_slug: str | None = "jiu-jitsu",
    graduation_snapshot: dict | None = None,
):
    return {
        "status": status,
        "timestamp": timestamp,
        "modalitySlug": modality_slug,
        "graduationSnapshot": graduation_snapshot,
    }


class TestCountsAndPercent:
    def test_confirmed_counts_as_confirmed(self):
        result = aggregate_attendance([_record("confirmed")], WINDOW)
        assert result["counts"]["confirmed"] == 1
        assert result["percent"] == 1.0

    def test_pending_counts_as_falta_in_denominator(self):
        records = [
            _record("confirmed"),
            _record("absent_justification_pending"),
        ]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"]["justificationPending"] == 1
        # denominator = confirmed(1) + absent(0) + pending(1) = 2
        assert result["percent"] == 0.5

    def test_justified_is_neutral_excluded_from_denominator(self):
        records = [
            _record("confirmed"),
            _record("absent_justified"),
        ]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"]["absentJustified"] == 1
        # denominator = confirmed(1) only; justified excluded entirely
        assert result["percent"] == 1.0

    def test_registered_excluded_from_percent(self):
        records = [
            _record("confirmed"),
            _record("registered"),
        ]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"]["awaitingConfirmation"] == 1
        # denominator = confirmed(1) only; registered excluded
        assert result["percent"] == 1.0

    def test_denominator_zero_yields_none_percent(self):
        records = [_record("absent_justified"), _record("registered")]
        result = aggregate_attendance(records, WINDOW)
        assert result["percent"] is None

    def test_empty_records_yields_none_percent_and_zero_counts(self):
        result = aggregate_attendance([], WINDOW)
        assert result["percent"] is None
        assert result["counts"] == {
            "confirmed": 0,
            "absent": 0,
            "absentJustified": 0,
            "justificationPending": 0,
            "awaitingConfirmation": 0,
        }

    def test_absent_counts_in_denominator(self):
        records = [_record("confirmed"), _record("absent")]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"]["absent"] == 1
        assert result["percent"] == 0.5

    def test_all_five_categories_are_distinct(self):
        records = [
            _record("confirmed"),
            _record("absent"),
            _record("absent_justified"),
            _record("absent_justification_pending"),
            _record("registered"),
        ]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"] == {
            "confirmed": 1,
            "absent": 1,
            "absentJustified": 1,
            "justificationPending": 1,
            "awaitingConfirmation": 1,
        }


class TestWindow:
    def test_record_before_window_excluded(self):
        records = [
            _record("confirmed", timestamp="2026-07-10T10:00:00-04:00"),
        ]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"]["confirmed"] == 0
        assert result["percent"] is None

    def test_record_at_window_start_included(self):
        records = [_record("confirmed", timestamp=WINDOW)]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"]["confirmed"] == 1

    def test_record_after_window_included(self):
        records = [
            _record("confirmed", timestamp="2026-07-20T10:00:00-04:00"),
        ]
        result = aggregate_attendance(records, WINDOW)
        assert result["counts"]["confirmed"] == 1


class TestFilters:
    def test_filter_by_month_keeps_only_matching_month(self):
        records = [
            _record("confirmed", timestamp="2026-07-16T10:00:00-04:00"),
            _record("confirmed", timestamp="2026-08-01T10:00:00-04:00"),
        ]
        result = aggregate_attendance(records, WINDOW, {"month": "2026-07"})
        assert result["counts"]["confirmed"] == 1

    def test_filter_by_modality(self):
        records = [
            _record("confirmed", modality_slug="jiu-jitsu"),
            _record("confirmed", modality_slug="muay-thai"),
        ]
        result = aggregate_attendance(
            records, WINDOW, {"modality": "jiu-jitsu"}
        )
        assert result["counts"]["confirmed"] == 1

    def test_filter_by_belt(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "purple",
                    "degree": 1,
                    "status": "approved",
                },
            ),
        ]
        result = aggregate_attendance(records, WINDOW, {"belt": "blue"})
        assert result["counts"]["confirmed"] == 1

    def test_filter_by_belt_excludes_no_graduation(self):
        records = [
            _record("confirmed", graduation_snapshot=None),
        ]
        result = aggregate_attendance(records, WINDOW, {"belt": "blue"})
        assert result["counts"]["confirmed"] == 0

    def test_filter_by_degree(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 3,
                    "status": "approved",
                },
            ),
        ]
        result = aggregate_attendance(records, WINDOW, {"degree": 2})
        assert result["counts"]["confirmed"] == 1

    def test_filters_combine(self):
        records = [
            _record(
                "confirmed",
                timestamp="2026-07-16T10:00:00-04:00",
                modality_slug="jiu-jitsu",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
            _record(
                "confirmed",
                timestamp="2026-07-16T10:00:00-04:00",
                modality_slug="muay-thai",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
        ]
        result = aggregate_attendance(
            records,
            WINDOW,
            {"month": "2026-07", "modality": "jiu-jitsu", "belt": "blue", "degree": 2},
        )
        assert result["counts"]["confirmed"] == 1


class TestByGraduation:
    def test_groups_by_belt_and_degree(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
            _record(
                "absent",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "purple",
                    "degree": 1,
                    "status": "approved",
                },
            ),
        ]
        result = aggregate_attendance(records, WINDOW)
        groups = {g["key"]: g for g in result["byGraduation"]}
        assert set(groups.keys()) == {"blue:2", "purple:1"}
        assert groups["blue:2"]["counts"]["confirmed"] == 1
        assert groups["blue:2"]["counts"]["absent"] == 1
        assert groups["blue:2"]["percent"] == 0.5
        assert groups["blue:2"]["pending"] is False
        assert groups["purple:1"]["counts"]["confirmed"] == 1
        assert groups["purple:1"]["percent"] == 1.0

    def test_null_snapshot_goes_to_no_graduation_bucket(self):
        records = [_record("confirmed", graduation_snapshot=None)]
        result = aggregate_attendance(records, WINDOW)
        groups = {g["key"]: g for g in result["byGraduation"]}
        assert set(groups.keys()) == {"no_graduation"}
        assert groups["no_graduation"]["counts"]["confirmed"] == 1

    def test_rejected_snapshot_goes_to_no_graduation_bucket(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "rejected",
                },
            )
        ]
        result = aggregate_attendance(records, WINDOW)
        groups = {g["key"]: g for g in result["byGraduation"]}
        assert set(groups.keys()) == {"no_graduation"}

    def test_mixed_graduation_and_no_graduation_records(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
            _record("confirmed", graduation_snapshot=None),
        ]
        result = aggregate_attendance(records, WINDOW)
        keys = {g["key"] for g in result["byGraduation"]}
        assert keys == {"blue:2", "no_graduation"}

    def test_pending_snapshot_status_marks_group_pending(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "pending",
                },
            )
        ]
        result = aggregate_attendance(records, WINDOW)
        groups = {g["key"]: g for g in result["byGraduation"]}
        assert groups["blue:2"]["pending"] is True

    def test_approved_snapshot_status_not_marked_pending(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            )
        ]
        result = aggregate_attendance(records, WINDOW)
        groups = {g["key"]: g for g in result["byGraduation"]}
        assert groups["blue:2"]["pending"] is False

    def test_byGraduation_entries_have_own_percent_using_same_formula(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
            _record(
                "absent_justification_pending",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            ),
        ]
        result = aggregate_attendance(records, WINDOW)
        groups = {g["key"]: g for g in result["byGraduation"]}
        # denominator = confirmed(1) + absent(0) + pending(1) = 2
        assert groups["blue:2"]["percent"] == 0.5

    def test_belt_and_degree_exposed_on_group(self):
        records = [
            _record(
                "confirmed",
                graduation_snapshot={
                    "belt": "blue",
                    "degree": 2,
                    "status": "approved",
                },
            )
        ]
        result = aggregate_attendance(records, WINDOW)
        group = result["byGraduation"][0]
        assert group["belt"] == "blue"
        assert group["degree"] == 2

    def test_no_graduation_group_has_null_belt_and_degree(self):
        records = [_record("confirmed", graduation_snapshot=None)]
        result = aggregate_attendance(records, WINDOW)
        group = result["byGraduation"][0]
        assert group["belt"] is None
        assert group["degree"] is None
