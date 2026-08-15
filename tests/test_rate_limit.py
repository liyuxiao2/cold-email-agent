"""Token bucket tests.

Requires a running Redis (docker compose up -d). Marked `integration` so the
unit suite stays offline-runnable.
"""

import time

import pytest

from cold_email.workers.shared.rate_limit import acquire

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clean_bucket():
    from cold_email.workers.shared.rate_limit import _redis

    _redis().delete("ratelimit:test")
    yield
    _redis().delete("ratelimit:test")


def test_burst_tokens_acquire_immediately():
    for _ in range(5):
        assert acquire("test", rate=1.0, burst=5, timeout=0) is True


def test_the_next_call_is_refused_once_the_burst_is_spent():
    for _ in range(5):
        acquire("test", rate=1.0, burst=5, timeout=0)
    assert acquire("test", rate=1.0, burst=5, timeout=0) is False


def test_tokens_refill_at_the_configured_rate():
    for _ in range(5):
        acquire("test", rate=10.0, burst=5, timeout=0)
    assert acquire("test", rate=10.0, burst=5, timeout=0) is False

    time.sleep(0.25)  # 10/s → ~2 tokens
    assert acquire("test", rate=10.0, burst=5, timeout=0) is True


def test_timeout_returns_false_rather_than_blocking_forever():
    for _ in range(2):
        acquire("test", rate=0.1, burst=2, timeout=0)

    started = time.monotonic()
    assert acquire("test", rate=0.1, burst=2, timeout=0.3) is False
    assert time.monotonic() - started < 1.0


def test_concurrent_acquirers_never_exceed_the_bucket():
    """The atomicity guarantee, and the reason for a Lua script.

    A read-then-write in Python has a race between worker processes that
    defeats the entire purpose of the limiter.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(lambda _: acquire("test", rate=0.001, burst=10, timeout=0), range(50))
        )
    assert sum(results) == 10


def test_fails_open_when_redis_is_unreachable(monkeypatch):
    """A limiter that fails CLOSED turns a Redis blip into a total drafting
    outage. Failing open risks provider 429s, which the fallback chain already
    handles — the cheaper failure wins."""
    import redis

    def boom(*args, **kwargs):
        raise redis.RedisError("connection refused")

    monkeypatch.setattr("cold_email.workers.shared.rate_limit._eval_bucket", boom)
    assert acquire("test", rate=1.0, burst=1, timeout=0) is True


def test_buckets_are_independent_per_key():
    """Each model in model_fallback_chain has its own provider quota."""
    from cold_email.workers.shared.rate_limit import _redis

    _redis().delete("ratelimit:other")
    try:
        for _ in range(3):
            acquire("test", rate=0.001, burst=3, timeout=0)
        assert acquire("test", rate=0.001, burst=3, timeout=0) is False
        assert acquire("other", rate=0.001, burst=3, timeout=0) is True
    finally:
        _redis().delete("ratelimit:other")
