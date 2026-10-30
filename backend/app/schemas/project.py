from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _check_dates(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise ValueError("end_date must not be before start_date")


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    goal: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    brief: str = Field(min_length=1)
    transcript: str | None = None

    @model_validator(mode="after")
    def _dates(self):
        _check_dates(self.start_date, self.end_date)
        return self


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    brief: str | None = Field(default=None, min_length=1)

    @field_validator("name", "brief")
    @classmethod
    def _not_null(cls, v):
        if v is None:
            raise ValueError("must not be null")
        return v

    @model_validator(mode="after")
    def _dates(self):
        _check_dates(self.start_date, self.end_date)
        return self


class SourceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    original_filename: str | None
    phase: str
    created_at: datetime


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    goal: str | None
    owner_id: int
    start_date: date | None
    end_date: date | None
    status: str
    created_at: datetime


class ProjectDetail(ProjectOut):
    brief: str | None
    sources: list[SourceSummary]
