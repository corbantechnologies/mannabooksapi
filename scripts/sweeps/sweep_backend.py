"""Inventory every backend route: method, path, accepted request fields, returned top-level keys."""
import ast
import inspect
import json
import re
import sys
import textwrap

sys.path.insert(0, ".")
from fastapi.routing import APIRoute  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.main import app  # noqa: E402


def iter_routes(routes, prefix=""):
    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):
            ctx = r.include_context
            p = getattr(ctx, "prefix", "") or ""
            yield from iter_routes(r.original_router.routes, prefix + p)


def model_fields(model) -> set[str]:
    names = set()
    for name, f in model.model_fields.items():
        names.add(name)
        if f.alias:
            names.add(f.alias)
        va = f.validation_alias
        gen = getattr(model.model_config.get("alias_generator"), "validation_alias", None)
        if va is None and callable(gen):
            va = gen(name)
        if va is not None:
            choices = getattr(va, "choices", None)
            if choices:
                names.update(c for c in choices if isinstance(c, str))
            elif isinstance(va, str):
                names.add(va)
    return names


def returned_keys(fn) -> set[str] | None:
    """Top-level keys of dict literals in `return {...}` statements (None if not determinable)."""
    try:
        src = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(src)
    except (OSError, TypeError, SyntaxError):
        return None
    keys: set[str] = set()
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            found = True
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
                elif k is None:  # **spread -> unknown extra keys
                    keys.add("*")
    return keys if found else None


out = []
for path, r in iter_routes(app.router.routes):
    body_fields, body_kind = None, None
    for p in inspect.signature(r.endpoint).parameters.values():
        ann = p.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            body_fields, body_kind = sorted(model_fields(ann)), ann.__name__
        elif getattr(ann, "__origin__", None) is list and ann.__args__ and isinstance(ann.__args__[0], type) and issubclass(ann.__args__[0], BaseModel):
            body_fields, body_kind = ["<list>"] + sorted(model_fields(ann.__args__[0])), "List[" + ann.__args__[0].__name__ + "]"
        elif ann in (dict,) or str(ann).startswith("typing.Dict") or (p.name in ("req", "payload", "body") and ann is inspect._empty):
            body_kind = "dict"
    resp = None
    if r.response_model is not None and isinstance(r.response_model, type) and issubclass(r.response_model, BaseModel):
        resp = sorted(r.response_model.model_fields.keys()) + ["<model:" + r.response_model.__name__ + ">"]
    else:
        keys = returned_keys(r.endpoint)
        resp = sorted(keys) if keys is not None else None
    params = inspect.signature(r.endpoint).parameters.values()
    query = [p.name for p in params if p.default is not inspect._empty and type(p.default).__name__ in ("Query", "QueryInfo")]
    required_query = [p.name for p in params if type(p.default).__name__ in ("Query", "QueryInfo") and p.default.is_required()]
    if body_kind == "dict":
        try:
            fsrc = inspect.getsource(r.endpoint)
            body_fields = sorted(set(re.findall(r"""(?:req|payload|body|data)(?:\.get\(|\[)\s*["']([A-Za-z_]\w*)["']""", fsrc)))
        except OSError:
            body_fields = None
    for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
        out.append({"method": m, "path": path, "body": body_fields, "bodyKind": body_kind, "response": resp,
                    "query": query, "requiredQuery": required_query, "func": r.endpoint.__module__ + "." + r.endpoint.__name__})

json.dump(out, open(sys.argv[1], "w"), indent=1)
print(len(out), "routes")
