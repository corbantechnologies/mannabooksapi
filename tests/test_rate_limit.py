import pytest
from fastapi import HTTPException

from app.core import rate_limit


@pytest.fixture
def memory_backend(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", None)
    monkeypatch.setattr(rate_limit, "_redis_initialised", True)
    rate_limit._buckets.clear()


async def test_memory_limit_blocks_after_limit(memory_backend):
    for _ in range(3):
        await rate_limit.hit("t:mem", limit=3, window_seconds=60)
    with pytest.raises(HTTPException) as exc:
        await rate_limit.hit("t:mem", limit=3, window_seconds=60)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) >= 1


async def test_reset_clears_counter(memory_backend):
    for _ in range(3):
        await rate_limit.hit("t:reset", limit=3, window_seconds=60)
    await rate_limit.reset("t:reset")
    await rate_limit.hit("t:reset", limit=3, window_seconds=60)


async def test_unreachable_redis_fails_open(monkeypatch):
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url("redis://127.0.0.1:1/0", socket_timeout=0.2, socket_connect_timeout=0.2)
    monkeypatch.setattr(rate_limit, "_redis", client)
    monkeypatch.setattr(rate_limit, "_redis_initialised", True)
    for _ in range(5):
        await rate_limit.hit("t:down", limit=1, window_seconds=60)
