"""Import every model module so Base.metadata is complete (used by Alembic and init scripts)."""
import importlib
import pkgutil

import app.models as _models_pkg
from app.db.base import Base

for _mod in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"app.models.{_mod.name}")

metadata = Base.metadata
