"""
Schema drift check: does the database contain everything the SQLAlchemy models use?

Fails (exit 1) when a model table/column, or a model enum value, is missing from the database.
That is exactly the class of bug where code ships before its migration (e.g. a new column or a new
journal_source value). Extra database objects are only reported, not failed.

Usage (uses DATABASE_URL like the app):
    python scripts/check_schema.py
"""
# Allow `python scripts/<name>.py` from the project root (adds the root to the import path).
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import asyncio  # noqa: E402
import sys  # noqa: E402

from sqlalchemy import Enum, text  # noqa: E402

import app.db.all_models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.db.schema_guard import expected_revision  # noqa: E402
from app.db.session import engine  # noqa: E402


async def main() -> int:
    problems: list[str] = []
    notes: list[str] = []
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'")
            )
        ).all()
        db_columns: dict[str, set[str]] = {}
        for t, c in rows:
            db_columns.setdefault(t, set()).add(c)

        enum_rows = (
            await conn.execute(
                text("SELECT t.typname, e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid")
            )
        ).all()
        db_enums: dict[str, set[str]] = {}
        for name, label in enum_rows:
            db_enums.setdefault(name, set()).add(label)

        has_version = (await conn.execute(text("SELECT to_regclass('public.alembic_version')"))).scalar()
        revision = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() if has_version else None
    await engine.dispose()

    expected = expected_revision()
    if revision != expected:
        problems.append(f"alembic revision is {revision or 'none'}, code expects {expected}")

    for table in Base.metadata.sorted_tables:
        if table.name not in db_columns:
            problems.append(f"missing table: {table.name}")
            continue
        for col in table.columns:
            if col.name not in db_columns[table.name]:
                problems.append(f"missing column: {table.name}.{col.name}")
            if isinstance(col.type, Enum) and col.type.name:
                missing = set(col.type.enums) - db_enums.get(col.type.name, set())
                if col.type.name not in db_enums:
                    problems.append(f"missing enum type: {col.type.name} (used by {table.name}.{col.name})")
                elif missing:
                    problems.append(f"enum {col.type.name} is missing values {sorted(missing)} (used by {table.name}.{col.name})")
        extra = db_columns[table.name] - {c.name for c in table.columns}
        if extra:
            notes.append(f"{table.name} has columns not in the model: {sorted(extra)}")

    for n in notes:
        print("note:", n)
    if problems:
        print(f"\nSCHEMA DRIFT: {len(set(problems))} problem(s)")
        for p in sorted(set(problems)):
            print(" -", p)
        return 1
    print(f"\nSchema OK: {len(Base.metadata.tables)} tables match the models at revision {revision}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
