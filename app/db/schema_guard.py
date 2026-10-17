# app/db/schema_guard.py
"""
Startup safety net for schema drift.

PostgreSQL enum values cannot be added by `CREATE TYPE ... IF NOT EXISTS` or `create_all`, so a database
that was created or updated outside Alembic can miss values the code relies on (e.g. journal entries of
type 'opening_balance' or 'payment'). This adds any missing values (idempotent) and warns loudly when the
database is not at the latest Alembic revision.
"""
import logging

from sqlalchemy import text

from app.db.session import engine

logger = logging.getLogger("mannabooks.schema")

REQUIRED_ENUM_VALUES = {
    "journal_source": ("document", "expense", "income", "payroll", "manual", "migrated",
                       "opening_balance", "year_end_close", "payment"),
}


async def ensure_runtime_schema() -> None:
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            for enum_name, values in REQUIRED_ENUM_VALUES.items():
                exists = (await conn.execute(text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": enum_name})).first()
                if not exists:
                    continue
                for value in values:
                    await conn.execute(text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'"))
    except Exception:
        logger.exception("Could not verify required enum values")

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        async with engine.connect() as conn:
            has_table = (await conn.execute(text("SELECT to_regclass('public.alembic_version')"))).scalar()
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() if has_table else None
        if current != head:
            logger.warning(
                "DATABASE SCHEMA IS BEHIND: alembic revision %s, code expects %s. Run `python -m alembic upgrade head`.",
                current or "none", head,
            )
    except Exception:
        logger.exception("Could not check the Alembic revision")
