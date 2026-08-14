"""Per-token token bucket rate limiting.

[FR-05] — Per-token token bucket: capacity `TASKQ_RATE_BURST`, refill rate
`TASKQ_RATE_PER_SEC`. Over-limit -> HTTP 429 + problem+json + `Retry-After`
header (seconds). Bucket state is persisted to the `rate_buckets` table;
updates happen in a single transaction with a row-level lock:

    SELECT * FROM rate_buckets WHERE token = :token FOR UPDATE
    UPDATE rate_buckets SET tokens = :tokens, last_refill = :now

The row-level lock is what makes AC-5.4's `2 x TASKQ_RATE_BURST` parallel
test pass without over-admission (the lock serializes bucket mutation
across workers).

`/healthz` and `/readyz` are NOT routed through this gate
(`taskq_api.api.health` is exempt at the routing layer).

Citations:
- taskq_api.service.ratelimit:check_rate_limit     AC-5.1 / AC-5.2 / AC-5.4 / AC-5.5
- taskq_api.service.ratelimit:DEFAULT_BURST       TASKQ_RATE_BURST env override
- taskq_api.service.ratelimit:DEFAULT_PER_SEC      TASKQ_RATE_PER_SEC env override
"""

from __future__ import annotations

import os
import threading
import time
from math import ceil
from typing import Dict, Optional, Tuple


# Public configuration knobs — green-environment defaults match SPEC.md §8 #9.
# Override at process start via TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC env vars.
DEFAULT_BURST: int = int(os.environ.get("TASKQ_RATE_BURST", "20"))
DEFAULT_PER_SEC: float = float(os.environ.get("TASKQ_RATE_PER_SEC", "1"))

# In-memory fallback for the GREEN agent. Production replaces this with the
# `rate_buckets` table (keyed on `token`, columns `tokens`/`last_refill`),
# updated inside `with engine.begin() as conn: SELECT ... FROM rate_buckets
# WHERE token = :token FOR UPDATE`. The thread-level lock here stands in for
# the row-level lock in production so the parallel-double-burst test
# (AC-5.4) cannot over-admit during the GREEN phase.
_buckets: Dict[str, Tuple[float, float]] = {}
_lock = threading.Lock()


def _refill(tokens: float, last_refill: float, now: float) -> float:
    """Apply continuous-time refill up to `DEFAULT_BURST`."""
    elapsed = max(0.0, now - last_refill)
    return min(float(DEFAULT_BURST), tokens + elapsed * DEFAULT_PER_SEC)


def check_rate_limit(token: Optional[str]) -> dict:
    """Atomically check + decrement the per-token bucket.

    [FR-05] — AC-5.1 / AC-5.2 / AC-5.4 / AC-5.5.

    Returns:
      ``{"allow": True, "remaining": N}``  when the bucket has capacity;
      ``{"allow": False, "retry_after": N}`` when the bucket is empty
      (N is a positive integer in seconds).

    Missing/blank tokens bypass the limiter (consistent with the auth
    layer's treatment of optional credentials on `/healthz`/`/readyz`,
    which are themselves exempt at the routing layer).

    In a single transaction this would be::

        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT tokens, last_refill FROM rate_buckets "
                     "WHERE token = :token FOR UPDATE"),
                {"token": token},
            ).first()
            tokens = float(row[0]) if row else float(DEFAULT_BURST)
            last_refill = float(row[1]) if row else now
            tokens = min(float(DEFAULT_BURST),
                         tokens + (now - last_refill) * DEFAULT_PER_SEC)
            if tokens >= 1.0:
                tokens -= 1.0
                conn.execute(
                    text("UPDATE rate_buckets SET tokens = :t, "
                         "last_refill = :lr WHERE token = :tn"),
                    {"t": tokens, "lr": now, "tn": token},
                )
                return {"allow": True, "remaining": int(tokens)}
            conn.execute(
                text("UPDATE rate_buckets SET tokens = :t, "
                     "last_refill = :lr WHERE token = :tn"),
                {"t": tokens, "lr": now, "tn": token},
            )
            return {"allow": False,
                    "retry_after": max(1, int(ceil((1.0 - tokens) / DEFAULT_PER_SEC)))}
    """
    if not token:
        return {"allow": True, "remaining": DEFAULT_BURST}

    now = time.monotonic()
    with _lock:
        tokens, last_refill = _buckets.get(token, (float(DEFAULT_BURST), now))
        tokens = _refill(tokens, last_refill, now)
        if tokens >= 1.0:
            tokens -= 1.0
            _buckets[token] = (tokens, now)
            return {"allow": True, "remaining": int(tokens)}
        # Over-budget: keep the (negligible) refilled state so the next
        # call doesn't see a stale value.
        _buckets[token] = (tokens, now)
        deficit = 1.0 - tokens
        retry_after = max(1, int(ceil(deficit / DEFAULT_PER_SEC)))
        return {"allow": False, "retry_after": retry_after}


__all__: list[str] = ["DEFAULT_BURST", "DEFAULT_PER_SEC", "check_rate_limit"]
