"""Graduation-system matrix — belt/degree rules per modality and age band.

Stored in the `graduation_systems` collection (one doc per modality,
id = "{projectId}_{slug}"), scoped by projectId (multi-tenant, ADR-13).
Seeded with sensible defaults and editable later. Jiu-Jitsu is age-banded
with up to 4 degrees per belt; Muay Thai / Capoeira are linear, belt-only.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Belt(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    order: int
    slug: str           # canonical key, e.g. "azul"
    name: str           # display, e.g. "Azul"
    color: str          # hex
    max_degree: int = 0  # 4 for jiu-jitsu, 0 for belt-only modalities


class AgeBand(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    min_age: int
    max_age: int        # inclusive; 200 = no upper bound
    belts: list[Belt]


class GraduationSystem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    modality_slug: str
    modality_name: str
    type: Literal["age_banded", "linear"]
    age_bands: list[AgeBand]


class GraduationSystemsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    systems: list[GraduationSystem]


# ── Dashboard ────────────────────────────────────────────────────────────────


class NextBelt(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    slug: str
    name: str
    color: str
    max_degree: int = 0


class GraduationStudentCard(BaseModel):
    """A student's graduation in the selected modality + resolved next steps."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    user_id: str
    name: str
    nickname: Optional[str] = None
    initials: str
    age: Optional[int] = None
    photo_url: Optional[str] = None
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    guardian_name: Optional[str] = None
    # Current graduation in this modality (may be empty = "sem graduação")
    belt: Optional[str] = None
    belt_name: Optional[str] = None
    color: Optional[str] = None
    degree: int = 0
    # values: "none" | "pending" | "approved"
    status: str = "none"
    # Resolved against the matrix + current age
    max_degree: int = 0
    can_add_degree: bool = False
    next_belt: Optional[NextBelt] = None
    out_of_band: bool = False  # current belt not found in the age band
    can_undo: bool = False     # a prior graduation action can be reverted


class GraduationDashboardOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    modality_slug: str
    modality_name: str
    has_system: bool
    students: list[GraduationStudentCard]


class GraduationActionRequest(BaseModel):
    """Body for approve/promote. `kind` only used by promote."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    modality: str                       # slug
    kind: Optional[Literal["degree", "belt"]] = None
