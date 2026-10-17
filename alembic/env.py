import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.all_models import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The database URL always comes from the application settings (.env / environment), never alembic.ini.
config.set_main_option("sqlalchemy.url", get_settings().async_database_url.replace("%", "%%"))
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# Any constant works; it only has to be the same for every process running migrations.
MIGRATION_LOCK_KEY = 72_310_451


def do_run_migrations(connection) -> None:
    # Serialise migrations across replicas/containers starting at the same time. The lock is
    # session-level, so it survives Alembic's own transactions; commit right away so Alembic
    # starts its migration transaction on a clean connection.
    connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
    connection.commit()
    try:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
        connection.commit()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
