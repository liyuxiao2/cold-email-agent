"""Fleet-wide token bucket in Redis, replacing time.sleep between LLM calls.

Why this is not a style change: a sleep paces ONE worker process. The actual
constraint is a provider quota shared by every worker, every user, and every task
type. With a single user the two were indistinguishable; with N users drafting
concurrently, sleep guarantees 429s while each process politely waits its own
turn.

The decrement runs as a Lua script so check-and-decrement is ATOMIC. A
read-then-write in Python has a race between processes that defeats the purpose.

Redis is already in the stack as the Celery broker, so this adds no
infrastructure.
"""

import logging
import time
from functools import lru_cache

import redis

from cold_email.config import settings

logger = logging.getLogger(__name__)

# Conservative defaults sized for a free tier. Override per model at the call site.
MODEL_RATE_DEFAULT = 0.5  # tokens per second
MODEL_BURST_DEFAULT = 5

_POLL_INTERVAL = 0.05

# Standard token bucket. Returns 1 if a token was taken, 0 otherwise.
# State is two hash fields (tokens, last_refill) plus a TTL so idle buckets expire.
_BUCKET_LUA = """
local key    = KEYS[1]
local rate   = tonumber(ARGV[1])
local burst  = tonumber(ARGV[2])
local now    = tonumber(ARGV[3])

local state       = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens      = tonumber(state[1])
local last_refill = tonumber(state[2])

if tokens == nil then
    tokens = burst
    last_refill = now
end

-- Refill for elapsed time, capped at burst.
local elapsed = math.max(0, now - last_refill)
tokens = math.min(burst, tokens + elapsed * rate)

local granted = 0
if tokens >= 1 then
    tokens = tokens - 1
    granted = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
-- Expire idle buckets: 2x the time to refill from empty, floored at 60s.
redis.call('EXPIRE', key, math.max(60, math.ceil(burst / rate) * 2))

return granted
"""


@lru_cache(maxsize=1)
def _redis() -> redis.Redis:
    """One connection pool per process."""
    return redis.Redis.from_url(settings.celery_broker_url, decode_responses=True)


@lru_cache(maxsize=1)
def _script():
    return _redis().register_script(_BUCKET_LUA)


def _eval_bucket(key: str, rate: float, burst: int) -> int:
    """Run one atomic take. Extracted so tests can simulate Redis being down."""
    return int(_script()(keys=[key], args=[rate, burst, time.time()]))


def acquire(
    key: str,
    rate: float = MODEL_RATE_DEFAULT,
    burst: int = MODEL_BURST_DEFAULT,
    timeout: float = 0.0,
) -> bool:
    """Take one token, waiting up to `timeout` seconds. True if granted.

    Fails OPEN if Redis is unreachable: a limiter that fails closed converts a
    Redis blip into a total drafting outage, whereas failing open risks provider
    429s that the model fallback chain already handles gracefully.
    """
    redis_key = f"ratelimit:{key}"
    deadline = time.monotonic() + timeout

    while True:
        try:
            if _eval_bucket(redis_key, rate, burst):
                return True
        except redis.RedisError as exc:
            logger.warning(f"Rate limiter unavailable ({exc}); failing open for {key}")
            return True

        if time.monotonic() >= deadline:
            logger.info(f"Rate limit timeout for {key} after {timeout}s")
            return False

        time.sleep(min(_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
