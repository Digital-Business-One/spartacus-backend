from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

SymptomFrequency = Literal["always", "sometimes", "never"]


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        raise ValueError("Data inválida: use DD/MM/YYYY")


class SymptomsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    coughing_blood: SymptomFrequency
    abdominal_pain: SymptomFrequency
    leg_pain: SymptomFrequency
    arm_pain: SymptomFrequency
    back_neck_pain: SymptomFrequency
    chest_pain: SymptomFrequency
    joint_pain: SymptomFrequency
    shortness_of_breath: SymptomFrequency
    feeling_weak: SymptomFrequency
    dizziness: SymptomFrequency
    heart_palpitation: SymptomFrequency


class DailyActivitiesIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    weekly_work_hours: Literal[
        "less_than_20", "20_to_40", "41_to_60", "more_than_60"
    ]
    work_activities: list[str] = Field(default_factory=list)
    work_activities_notes: str = ""


class MedicalHistoryIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    last_medical_exam_date: str = ""
    family_heart_disease: list[str] = Field(default_factory=list)
    surgeries: list[str] = Field(default_factory=list)
    surgeries_other: str = ""
    diagnosed_conditions: list[str] = Field(default_factory=list)
    diagnosed_conditions_other: str = ""
    current_medications: str = ""
    symptoms: SymptomsIn
    has_allergies: bool = False
    allergies_details: str = ""
    has_recent_injury: bool = False
    injury_details: str = ""
    has_exercise_restriction: bool = False
    restriction_details: str = ""

    @field_validator("last_medical_exam_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        if v:
            _parse_date(v)
        return v

    @model_validator(mode="after")
    def check_conditional_fields(self) -> "MedicalHistoryIn":
        if self.has_allergies and not self.allergies_details.strip():
            raise ValueError("Detalhe das alergias é obrigatório")
        if self.has_recent_injury and not self.injury_details.strip():
            raise ValueError("Detalhe da lesão é obrigatório")
        if self.has_exercise_restriction and not self.restriction_details.strip():
            raise ValueError("Detalhe da restrição é obrigatório")
        return self


class HealthBehaviorIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    smokes: bool = False
    cigarettes_per_day: str = ""
    practices_physical_activity: bool = False
    physical_activity_description: str = ""
    physical_activity_frequency: str = ""
    physical_activity_duration: str = ""

    @model_validator(mode="after")
    def check_conditional_fields(self) -> "HealthBehaviorIn":
        if self.smokes and not self.cigarettes_per_day.strip():
            raise ValueError("Quantidade de cigarros é obrigatória")
        if self.practices_physical_activity:
            if not self.physical_activity_description.strip():
                raise ValueError("Descrição da atividade é obrigatória")
        return self


class MedicalHistoryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    daily_activities: Optional[DailyActivitiesIn] = Field(default=None)
    medical_history: MedicalHistoryIn
    health_behavior: HealthBehaviorIn
    goals: list[str]
    goals_other: str = ""
    general_comments: str = ""

    @field_validator("goals")
    @classmethod
    def goals_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Selecione ao menos um objetivo")
        return v

    @model_validator(mode="after")
    def check_goals_other(self) -> "MedicalHistoryRequest":
        if "other" in self.goals and not self.goals_other.strip():
            raise ValueError("Descreva o objetivo 'Outro'")
        return self


class MedicalHistoryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    project_id: str
    user_id: str
    status: str
    daily_activities: Optional[DailyActivitiesIn] = Field(default=None)
    medical_history: MedicalHistoryIn
    health_behavior: HealthBehaviorIn
    goals: list[str]
    goals_other: str = ""
    general_comments: str = ""
    filled_at: Optional[str] = None
    filled_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    review_note: Optional[str] = None


class MedicalHistoryReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    action: Literal["approve", "request_revision"]
    note: str = ""

    @model_validator(mode="after")
    def note_required_on_revision(self) -> "MedicalHistoryReviewRequest":
        if self.action == "request_revision" and not self.note.strip():
            raise ValueError("Informe o motivo da revisão")
        return self


class PendingReviewItem(BaseModel):
    """A user whose medical history is pending staff review."""
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    name: str
    submitted_at: Optional[str] = None


class PendingReviewList(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    items: list[PendingReviewItem]


class PendingAnamneseItem(BaseModel):
    """A user (self or dependent) that needs medical history filled."""
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    name: str
    birth_date: str
    is_self: bool
    is_dependent: bool


class PendingAnamneseList(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    pending: list[PendingAnamneseItem]
