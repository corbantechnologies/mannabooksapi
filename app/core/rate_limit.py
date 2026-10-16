# app/core/rate_limit.py
"""
Rate limiting for sensitive endpoints (login, registration, password reset).

Backed by Redis when REDIS_URL is set, so limits are shared across all API instances/workers.
Without REDIS_URL (local development, tests) it falls back to a per-process in-memory window.
If Redis is configured but unreachable, requests are allowed (fail-open) and the error is logged,
so a Redis outage cannot lock everyone out of the app.
"""
import logging
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.security import constant_time_equals

logger = logging.getLogger("mannabooks.rate_limit")

_KEY_PREFIX = "mb:rl:"
_redis = None
_redis_initialised = False

_buckets: dict[str, deque] = defaultdict(deque)
_lock = Lock()
_MAX_KEYS = 50_000


def _get_redis():
    """Lazily create one shared async Redis client (None when REDIS_URL is not configured)."""
    global _redis, _redis_initialised
    if not _redis_initialised:
        _redis_initialised = True
        url = get_settings().REDIS_URL
        if url:
            import redis.asyncio as redis_asyncio

            _redis = redis_asyncio.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)
    return _redis


def client_ip(request: Request) -> str:
    """Best-effort client IP. The Next.js server forwards the browser IP in X-Client-IP (trusted only with the internal key)."""
    settings = get_settings()
    if constant_time_equals(request.headers.get("x-internal-key"), settings.INTERNAL_API_KEY):
        forwarded = request.headers.get("x-client-ip")
        if forwarded:
            return forwarded.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # The right-most entry is the one appended by our own edge proxy.
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please wait a few minutes and try again.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def _hit_memory(key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    with _lock:
        if len(_buckets) > _MAX_KEYS:
            _buckets.clear()
        bucket = _buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise _too_many(int(window_seconds - (now - bucket[0])) + 1)
        bucket.append(now)


async def hit(key: str, limit: int, window_seconds: int) -> None:
    """Record one attempt for `key`; raise 429 when more than `limit` happened within the window."""
    client = _get_redis()
    if client is None:
        _hit_memory(key, limit, window_seconds)
        return

    rkey = _KEY_PREFIX + key
    try:
        pipe = client.pipeline(transaction=True)
        pipe.incr(rkey)
        pipe.expire(rkey, window_seconds, nx=True)
        pipe.ttl(rkey)
        count, _, ttl = await pipe.execute()
    except Exception:
        logger.exception("Redis rate limiter unavailable; allowing request for %s", key)
        return

    if int(count) > limit:
        raise _too_many(int(ttl) if ttl and int(ttl) > 0 else window_seconds)


async def reset(key: str) -> None:
    client: Optional[object] = _get_redis()
    if client is None:
        with _lock:
            _buckets.pop(key, None)
        return
    try:
        await client.delete(_KEY_PREFIX + key)
    except Exception:
        logger.exception("Redis rate limiter reset failed for %s", key)
