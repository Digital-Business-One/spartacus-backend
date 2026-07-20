"""Justification-type catalog — configurable per project.

Stored in the `justification_types` collection (one doc per type,
id = "{projectId}_{slug}"), scoped by projectId (multi-tenant, ADR-13).
Mirrors the `graduation_systems` convention. Sub-projeto B (Justificativa
de Faltas): types feed the app's "Justificar" flow (student/guardian pick a
type; `requiresAttachment` gates the upload step) and are managed via
backoffice CRUD. Inactive types are hidden from students, but past
justifications keep the type name denormalized on the `attendance` doc
(see `app.models.attendance.Justification`), so deactivation never deletes.

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(section "Tipos de justificativa").
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class JustificationType(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    project_id: str
    slug: str
    name: str
    allows_attachment: bool = False
    requires_attachment: bool = False
    active: bool = True
    order: int = 0


class JustificationTypesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    types: list[JustificationType]


class JustificationTypeUpsertRequest(BaseModel):
    """Body for `PUT /projects/{id}/justification-types/{slug}` (staff)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    name: str
    allows_attachment: bool = False
    requires_attachment: bool = False
    active: bool = True
    order: int = 0
