"""Per-token token bucket rate limiting.

[FR-05] — Per-token token bucket: capacity ``TASKQ_RATE_BURST``, refill
rate ``TASKQ_RATE_PER_SEC``. Over-limit -> HTTP 429 + problem+json +
``Retry-After`` header (seconds). Bucket state lives in the
``rate_buckets`` table (see ``taskq_api.repository.rate_repo``); each
update acquires a row-level lock (``SELECT tokens, last_refill FROM
rate_buckets WHERE token = :token FOR UPDATE``) inside a single
``engine.begin()`` transaction so parallel admission attempts cannot
over-admit.

``/healthz`` and ``/readyz`` are NOT routed through this gate
(``taskq_api.api.health`` is exempt at the routing layer).

Citations:
- taskq_api.service.ratelimit:check_rate_limit  AC-5.1 / AC-5.2 / AC-5.4 / AC-5.5
- taskq_api.service.ratelimit:DEFAULT_BURST    TASKQ_RATE_BURST env override
- taskq_api.service.ratelimit:DEFAULT_PER_SEC  TASKQ_RATE_PER_SEC env override
- taskq_api.repository.rate_repo:try_consume   AC-5.5 (FOR UPDATE row lock)
"""

from __future__ import annotations

# pragma: no error-handling — bucket arithmetic only. Storage access
# is delegated to taskq_api.repository.rate_repo, which owns the
# handler for it; failing open or closed here would be a policy
# decision this layer is not entitled to make.

import os
import time
from math import ceil
from typing import Optional

from taskq_api.repository import rate_repo


# Public configuration knobs — green-environment defaults match SPEC.md §8 #9.
# Override at process start via TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC env vars.
DEFAULT_BURST: int = int(os.environ.get("TASKQ_RATE_BURST", "20"))
DEFAULT_PER_SEC: float = float(os.environ.get("TASKQ_RATE_PER_SEC", "1"))


def _retry_after_seconds(refilled_level: float) -> int:
    """Positive integer seconds until the bucket carries >= 1 token."""
    deficit = 1.0 - refilled_level
    return max(1, int(ceil(deficit / DEFAULT_PER_SEC)))


def check_rate_limit(token: Optional[str]) -> dict:
    """Atomically check and consume the per-token bucket slot.

    [FR-05] — AC-5.1 / AC-5.2 / AC-5.4 / AC-5.5.

    Returns ``{"allow": True, "remaining": N}`` when the bucket has
    capacity, or ``{"allow": False, "retry_after": N}`` when over
    budget (N is a positive integer in seconds; AC-5.2).

    Missing or blank tokens bypass the limiter. ``/healthz`` and
    ``/readyz`` are exempt at the routing layer (see
    ``taskq_api.api.health``), so this branch never sees those paths.
    """
    if not token:
        return {"allow": True, "remaining": DEFAULT_BURST}

    allowed, level = rate_repo.try_consume(
        token=token,
        now=time.monotonic(),
        burst=DEFAULT_BURST,
        per_sec=DEFAULT_PER_SEC,
    )
    if allowed:
        return {"allow": True, "remaining": int(level)}
    return {"allow": False, "retry_after": _retry_after_seconds(level)}


# ---------------------------------------------------------------------------
# Back-compat reset hook.
#
# The autouse fixtures in ``03-development/tests/conftest.py`` and
# ``03-development/tests/test_fr05.py`` reset in-process rate-limit state
# between tests by calling ``_rl._buckets.clear()``. To keep that contract
# honest after moving storage into ``rate_repo``, expose ``_buckets`` as an
# object whose ``.clear()`` wipes the underlying ``rate_buckets`` table.
# Production callers should NOT depend on this symbol — it is a
# test-fixture bridge only.
# ---------------------------------------------------------------------------
class _BucketStateHolder:
    """Test-fixture bridge: ``_rl._buckets.clear()`` -> rate_repo.reset_for_test()."""

    def clear(self) -> None:
        rate_repo.reset_for_test()


_buckets = _BucketStateHolder()


__all__: list[str] = ["DEFAULT_BURST", "DEFAULT_PER_SEC", "check_rate_limit"]
