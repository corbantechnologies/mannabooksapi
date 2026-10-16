"""Find ORM misuse: models constructed with unknown fields, and unknown attributes set/read on loaded rows."""
import ast
import glob
import sys

sys.path.insert(0, ".")
from sqlalchemy import inspect as sa_inspect  # noqa: E402

import app.db.all_models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402

models = {}
for mapper in Base.registry.mappers:
    cls = mapper.class_
    names = {a.key for a in mapper.attrs} | set(dir(cls))
    models[cls.__name__] = names

findings = []


class FuncVisitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path

    def visit_FunctionDef(self, node):
        self.check_function(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def model_of(self, value):
        """Model name if `value` is Model(...), await db.get(Model, ...), or select(Model)...scalar*()."""
        if isinstance(value, ast.Await):
            value = value.value
        if isinstance(value, ast.Call):
            f = value.func
            if isinstance(f, ast.Name) and f.id in models:
                return f.id
            if isinstance(f, ast.Attribute) and f.attr == "get" and value.args and isinstance(value.args[0], ast.Name) and value.args[0].id in models:
                return value.args[0].id
        return None

    def select_model(self, value):
        """Model if the expression is a select(Model)... query result (scalar_one_or_none / scalars().all() / first)."""
        for n in ast.walk(value):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "select" and n.args:
                a = n.args[0]
                if isinstance(a, ast.Name) and a.id in models and len(n.args) == 1:
                    return a.id
        return None

    def check_function(self, fn):
        var_model = {}
        list_model = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                m = self.select_model(node.value)
                src = ast.unparse(node.value)
                if m and "execute(" in src:
                    if ".all()" in src or "scalars()" in src and "first()" not in src and "one_or_none" not in src:
                        list_model[node.targets[0].id] = m
                    else:
                        var_model[node.targets[0].id] = m
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                it = node.iter
                if isinstance(it, ast.Name) and it.id in list_model:
                    var_model[node.target.id] = list_model[it.id]
            if isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Name) and node.iter.id in list_model:
                var_model[node.target.id] = list_model[node.iter.id]
        for node in ast.walk(fn):
            # constructor keyword check
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in models:
                for kw in node.keywords:
                    if kw.arg and kw.arg not in models[node.func.id]:
                        findings.append(f"{self.path}:{node.lineno} {node.func.id}(...) has no field '{kw.arg}'")
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                m = self.model_of(node.value)
                if m:
                    var_model[node.targets[0].id] = m
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in var_model:
                m = var_model[node.value.id]
                if node.attr not in models[m] and not node.attr.startswith("_"):
                    kind = "sets" if isinstance(node.ctx, ast.Store) else "reads"
                    findings.append(f"{self.path}:{node.lineno} {kind} {m}.{node.attr} (no such field)")


for path in glob.glob("app/**/*.py", recursive=True):
    tree = ast.parse(open(path, encoding="utf-8").read())
    FuncVisitor(path.replace("\\", "/")).visit(tree)

for f in sorted(set(findings)):
    print(f)
print(len(set(findings)), "findings")
