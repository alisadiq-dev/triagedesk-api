from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import Priority

CategoryName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
CategoryDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class Category(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName
    description: CategoryDescription | None = None


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName | None = None
    description: CategoryDescription | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        return self


class SlaPolicy(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    priority: Priority
    response_hours: int
    resolution_hours: int
    updated_at: datetime


class SlaPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_hours: int | None = Field(default=None, gt=0, le=100_000)
    resolution_hours: int | None = Field(default=None, gt=0, le=100_000)

    @model_validator(mode="after")
    def at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        return self
