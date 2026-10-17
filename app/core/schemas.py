# app/core/schemas.py
import types
import typing

from pydantic import AliasChoices, AliasGenerator, BaseModel, ConfigDict, ValidationInfo, field_validator
from pydantic.alias_generators import to_camel


def _blank_means_none(annotation) -> bool:
    """True for Optional fields that are not plain text (UUID, date, number...), where "" can only mean "not set"."""
    if typing.get_origin(annotation) not in (typing.Union, types.UnionType):
        return False
    args = typing.get_args(annotation)
    return type(None) in args and str not in args


class ApiModel(BaseModel):
    """
    Base for API models. Accepts both snake_case and camelCase input (`full_name` or `fullName`),
    because the web app sends either. Output field names are unchanged. Fields with an explicit
    alias keep it.

    Optional non-text fields treat an empty string as null: forms send "" for an unselected
    dropdown (e.g. no client on a walk-in invoice), which would otherwise fail UUID/date parsing.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=AliasGenerator(validation_alias=lambda name: AliasChoices(name, to_camel(name))),
    )

    @field_validator("*", mode="before")
    @classmethod
    def _empty_string_to_none(cls, v, info: ValidationInfo):
        if isinstance(v, str) and not v.strip() and info.field_name:
            field = cls.model_fields.get(info.field_name)
            if field is not None and _blank_means_none(field.annotation):
                return None
        return v
