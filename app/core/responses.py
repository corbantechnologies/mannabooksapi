# app/core/responses.py
"""
Compatibility JSON response: every snake_case key is also exposed in camelCase
(`expected_close_date` -> `expectedCloseDate`), recursively. Existing keys are never overwritten.

Many endpoints return database rows directly (snake_case) while the web app reads camelCase in
places. This keeps both working until typed API clients replace the ad-hoc field access.
"""
import re
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


# Only real field names (lowercase snake_case); data-valued keys like "PROPOSAL_SENT" are left alone.
_SNAKE_FIELD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def add_camel_keys(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k] = add_camel_keys(v)
        for k in list(out.keys()):
            if isinstance(k, str) and _SNAKE_FIELD.match(k):
                ck = _camel(k)
                if ck not in out:
                    out[ck] = out[k]
        return out
    if isinstance(value, list):
        return [add_camel_keys(v) for v in value]
    return value


class CompatJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(add_camel_keys(jsonable_encoder(content)))
