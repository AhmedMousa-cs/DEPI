"""PM decision contract. Validated before it can reach the graph."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class InvalidDecision(ValueError):
    pass


class PMDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approved", "rejected", "modified"]
    comment: str = Field(default="", max_length=2000)
    decided_by: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def _comment_required(self):
        if self.decision in ("rejected", "modified") and not self.comment:
            raise ValueError("a comment is required when rejecting or modifying")
        return self


def parse_decision(raw) -> PMDecision:
    if isinstance(raw, PMDecision):
        return raw
    try:
        return PMDecision.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise InvalidDecision(f"{loc}: {first['msg']}" if loc else first["msg"]) from None
