"""Tests for the graduation roster (grouped-by-guardian) assembly."""

from app.models.graduation_system import (
    RosterFamily,
    RosterOut,
    RosterPerson,
    RosterTurma,
)


def test_roster_models_use_camel_aliases():
    person = RosterPerson(
        user_id="u1", display_name="Zé", name="José", initials="J",
        photo_url=None, age=11, is_dependent=True, guardian_uid="g1",
        turmas=[RosterTurma(modality_name="Jiu-Jitsu", class_name="Infantil")],
        graduations=[],
    )
    fam = RosterFamily(guardian=person, guardian_is_student=False, dependents=[])
    out = RosterOut(families=[fam], pending_count=3)
    dumped = out.model_dump(by_alias=True)
    assert dumped["pendingCount"] == 3
    assert dumped["families"][0]["guardianIsStudent"] is False
    assert dumped["families"][0]["guardian"]["displayName"] == "Zé"
    assert dumped["families"][0]["guardian"]["turmas"][0]["className"] == "Infantil"
