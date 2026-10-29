from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clean_skills(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for s in values or []:
        s = s.strip()
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)
    return out


def _check_dates(a: date | None, b: date | None) -> None:
    if a and b and b < a:
        raise ValueError("available_to must not be before available_from")


class TeamMemberCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    role_title: str | None = Field(default=None, max_length=120)
    skills: list[str] = Field(default_factory=list)
    capacity_hours_per_week: float = Field(default=40, ge=0, le=168)
    available_from: date | None = None
    available_to: date | None = None

    @field_validator("skills")
    @classmethod
    def _skills(cls, v):
        return _clean_skills(v)

    @model_validator(mode="after")
    def _dates(self):
        _check_dates(self.available_from, self.available_to)
        return self


class TeamMemberUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    role_title: str | None = Field(default=None, max_length=120)
    skills: list[str] | None = None
    capacity_hours_per_week: float | None = Field(default=None, ge=0, le=168)
    available_from: date | None = None
    available_to: date | None = None

    @field_validator("name", "skills", "capacity_hours_per_week")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v

    @field_validator("skills")
    @classmethod
    def _skills(cls, v):
        return _clean_skills(v)

    @model_validator(mode="after")
    def _dates(self):
        _check_dates(self.available_from, self.available_to)
        return self


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    role_title: str | None
    skills: list[str]
    capacity_hours_per_week: float
    available_from: date | None
    available_to: date | None
    user_id: int | None
    is_leader: bool
    user_email: str | None = None


class LeaderAssign(BaseModel):
    member_id: int
    user_email: str = Field(min_length=3)


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
