"""
Refresh-token rotation rules, tested against an in-memory SQLite database containing only the
`users` and `refresh_tokens` tables (the logic under test is plain SQL, no Postgres features).
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.identity import RefreshToken, User
from app.modules.auth import tokens
from app.modules.auth.tokens import RefreshError, rotate, start_session


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: User.__table__.create(c))
        await conn.run_sync(lambda c: RefreshToken.__table__.create(c))
    session = async_sessionmaker(engine, expire_on_commit=False)()
    yield session
    await session.close()
    await engine.dispose()


@pytest.fixture
async def user(db):
    u = User(id=uuid.uuid4(), name="Test", email=f"{uuid.uuid4().hex}@x.test", password_hash="x")
    db.add(u)
    await db.commit()
    return u


async def _age(db, raw, **delta):
    row = (await db.execute(
        RefreshToken.__table__.select().where(RefreshToken.token_hash == tokens.hash_token(raw))
    )).first()
    await db.execute(
        RefreshToken.__table__.update()
        .where(RefreshToken.id == row.id)
        .values(created_at=row.created_at - timedelta(**delta))
    )
    await db.commit()


async def test_rotation_returns_new_tokens_and_retires_old(db, user):
    first = await start_session(db, user.id)
    await db.commit()
    _, second = await rotate(db, first.refresh_token)
    assert second.refresh_token != first.refresh_token
    assert second.expires_in == tokens.get_settings().JWT_EXPIRE_MINUTES * 60
    _, third = await rotate(db, second.refresh_token)
    assert third.refresh_token not in (first.refresh_token, second.refresh_token)


async def test_parallel_refresh_within_grace_is_allowed(db, user):
    first = await start_session(db, user.id)
    await db.commit()
    await rotate(db, first.refresh_token)
    _, sibling = await rotate(db, first.refresh_token)  # second tab, same moment
    assert sibling.refresh_token


async def test_reuse_after_grace_revokes_whole_family(db, user, monkeypatch):
    monkeypatch.setattr(tokens.get_settings(), "REFRESH_REUSE_GRACE_SECONDS", 0)
    first = await start_session(db, user.id)
    await db.commit()
    _, second = await rotate(db, first.refresh_token)
    with pytest.raises(RefreshError):
        await rotate(db, first.refresh_token)  # stolen copy replayed
    with pytest.raises(RefreshError):
        await rotate(db, second.refresh_token)  # legitimate token is now dead too


async def test_idle_timeout(db, user):
    first = await start_session(db, user.id)
    await db.commit()
    await _age(db, first.refresh_token, hours=12, minutes=1)
    with pytest.raises(RefreshError):
        await rotate(db, first.refresh_token)


async def test_active_user_survives_long_shift(db, user):
    """A stock count that refreshes every 15 minutes for 11 hours stays signed in."""
    current = await start_session(db, user.id)
    await db.commit()
    for _ in range(44):
        await _age(db, current.refresh_token, minutes=15)
        _, current = await rotate(db, current.refresh_token)
    assert current.refresh_token


async def test_absolute_expiry(db, user):
    first = await start_session(db, user.id)
    await db.commit()
    await db.execute(
        RefreshToken.__table__.update().values(expires_at=datetime.utcnow() - timedelta(seconds=1))
    )
    await db.commit()
    with pytest.raises(RefreshError):
        await rotate(db, first.refresh_token)


async def test_signed_out_everywhere(db, user):
    first = await start_session(db, user.id)
    await db.commit()
    await tokens.revoke_all_for_user(db, user.id, "logout_all")
    await db.commit()
    with pytest.raises(RefreshError):
        await rotate(db, first.refresh_token)


async def test_unknown_token(db):
    with pytest.raises(RefreshError):
        await rotate(db, "not-a-real-refresh-token-value")
