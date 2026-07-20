"""Tests for the graduation roster (grouped-by-guardian) assembly."""

from app.models.graduation_system import (
    AgeBand,
    Belt,
    GraduationSystem,
    RosterFamily,
    RosterOut,
    RosterPerson,
    RosterTurma,
)
from app.services.graduation_service import GraduationService


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


_JIU = GraduationSystem(
    modality_slug="jiu-jitsu", modality_name="Jiu-Jitsu", type="age_banded",
    age_bands=[
        AgeBand(min_age=4, max_age=15, belts=[
            Belt(order=0, slug="branca", name="Branca", color="#f2f2f2", max_degree=4),
            Belt(order=1, slug="cinza", name="Cinza", color="#a8a8a8", max_degree=4),
        ]),
        AgeBand(min_age=16, max_age=200, belts=[
            Belt(order=0, slug="branca", name="Branca", color="#f2f2f2", max_degree=4),
            Belt(order=1, slug="azul", name="Azul", color="#1f4fa0", max_degree=4),
        ]),
    ],
)
_SYSTEMS = {"jiu-jitsu": _JIU}


def _p(uid, name, roles, birth="01/01/2015", grad=None, enrolled=None,
       guardian=None, nickname=None):
    return {
        "uid": uid, "name": name, "nickname": nickname, "birthDate": birth,
        "photoUrl": None, "guardianUid": guardian, "roles": set(roles),
        "graduation": grad or {}, "enrolledSlugs": set(enrolled or []),
        "turmas": [],
    }


class TestToRosterPerson:
    def setup_method(self):
        self.svc = GraduationService()

    def test_pending_entry_becomes_card_with_modality(self):
        p = _p("d1", "Lucas", ["student"], birth="01/01/2015",
               grad={"jiu-jitsu": {"belt": "branca", "degree": 0, "status": "pending"}})
        person = self.svc._to_roster_person(p, _SYSTEMS)
        assert len(person.graduations) == 1
        card = person.graduations[0]
        assert card.modality_slug == "jiu-jitsu"
        assert card.modality_name == "Jiu-Jitsu"
        assert card.status == "pending"
        assert card.next_belt.slug == "cinza"   # 10yo → kids band

    def test_enrolled_without_system_yields_no_card(self):
        # MMA: enrolled, no system, no entry → nenhum bloco de graduação
        p = _p("s1", "Rafa", ["student"], enrolled={"mma"})
        person = self.svc._to_roster_person(p, _SYSTEMS)
        assert person.graduations == []

    def test_no_entry_no_enrollment_is_empty(self):
        p = _p("s2", "Ana", ["student"])
        person = self.svc._to_roster_person(p, _SYSTEMS)
        assert person.graduations == []


class TestAssembleRoster:
    def setup_method(self):
        self.svc = GraduationService()

    def test_guardian_student_nests_dependent_single_family(self):
        guardian = _p(
            "g1", "Carlos", ["guardian", "student"], birth="01/01/1990",
            grad={"jiu-jitsu": {"belt": "azul", "degree": 2, "status": "approved"}},
        )
        dep = _p(
            "d1", "Lucas", ["student"], birth="01/01/2015", guardian="g1",
            grad={"jiu-jitsu": {"belt": "branca", "degree": 0, "status": "pending"}},
        )
        out = self.svc.assemble_roster([guardian, dep], _SYSTEMS)
        assert len(out.families) == 1
        fam = out.families[0]
        assert fam.guardian.user_id == "g1"
        assert fam.guardian_is_student is True
        assert len(fam.guardian.graduations) == 1        # item 4: guardian's own grad
        assert [d.user_id for d in fam.dependents] == ["d1"]
        assert out.pending_count == 1                    # dependent's pending

    def test_non_student_guardian_has_no_graduations(self):
        guardian = _p(
            "g2", "Marina", ["guardian"], birth="01/01/1985",
            grad={"jiu-jitsu": {"belt": "azul", "degree": 0, "status": "approved"}},
        )
        dep = _p(
            "d2", "Sofia", ["student"], birth="01/01/2016", guardian="g2",
            grad={"jiu-jitsu": {"belt": "branca", "degree": 3, "status": "approved"}},
        )
        out = self.svc.assemble_roster([guardian, dep], _SYSTEMS)
        fam = out.families[0]
        assert fam.guardian.user_id == "g2"
        assert fam.guardian_is_student is False          # item 7
        assert fam.guardian.graduations == []            # item 7: sem graduação própria
        assert len(fam.dependents) == 1

    def test_standalone_student_is_own_family(self):
        s = _p("s1", "Rafael", ["student"], birth="01/01/2000",
               grad={"jiu-jitsu": {"belt": "azul", "degree": 0, "status": "approved"}})
        out = self.svc.assemble_roster([s], _SYSTEMS)
        assert len(out.families) == 1
        assert out.families[0].guardian.user_id == "s1"
        assert out.families[0].dependents == []

    def test_non_student_non_guardian_is_skipped(self):
        sup = _p("x1", "Doador", ["supporter"], birth="01/01/1970")
        out = self.svc.assemble_roster([sup], _SYSTEMS)
        assert out.families == []
