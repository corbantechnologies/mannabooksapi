# app/core/schemas.py
from pydantic import AliasChoices, AliasGenerator, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """
    Base for API models. Accepts both snake_case and camelCase input (`full_name` or `fullName`),
    because the web app sends either. Output field names are unchanged. Fields with an explicit
    alias keep it.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=AliasGenerator(validation_alias=lambda name: AliasChoices(name, to_camel(name))),
    )
