from datetime import datetime

from pydantic import BaseModel


class SourceOut(BaseModel):
    id: int
    project_id: int
    kind: str
    title: str
    original_filename: str | None
    phase: str
    created_at: datetime
    content_length: int


class SourceDetail(SourceOut):
    content_text: str
