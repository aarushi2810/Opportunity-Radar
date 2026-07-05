"""Redis-backed deduplication cache with content hash and TTL."""

from __future__ import annotations

import hashlib
import logging
import time

import config

logger = logging.getLogger("opportunity_radar.dedup")

# ── Redis Setup ───────────────────────────────────────────────────────────────

try:
    import redis as redis_lib
    _redis_url = config.REDIS_URL
    if _redis_url:
        _redis = redis_lib.from_url(_redis_url, decode_responses=True)
        logger.info("DedupCache: Redis (%s)", _redis_url[:30])
    else:
        import fakeredis
        _redis = fakeredis.FakeRedis(decode_responses=True)
        logger.info("DedupCache: fakeredis (local dev)")
except Exception as exc:
    import fakeredis
    _redis = fakeredis.FakeRedis(decode_responses=True)
    logger.warning("Redis init failed, using fakeredis: %s", exc)


class DedupCache:
    """Content-hash deduplication using Redis with configurable TTL."""

    def __init__(self):
        self._ttl_seconds = config.DEDUP_TTL_HOURS * 3600
        logger.info("DedupCache initialized (TTL: %dh)", config.DEDUP_TTL_HOURS)

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute a short content hash for dedup keying."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def is_duplicate(self, content_hash: str) -> bool:
        """Return True if this content hash has been seen within the TTL window."""
        key = f"dedup:{content_hash}"
        try:
            return _redis.exists(key) == 1
        except Exception:
            return False

    def mark_seen(self, content_hash: str):
        """Record a content hash as seen with TTL expiry."""
        key = f"dedup:{content_hash}"
        try:
            _redis.setex(key, self._ttl_seconds, str(time.time()))
        except Exception as exc:
            logger.debug("DedupCache mark_seen error: %s", exc)

    def check_and_mark(self, content: str) -> bool:
        """Check duplicate status and mark as seen atomically.

        Returns True if the content is a duplicate (should be skipped).
        """
        content_hash = self.compute_hash(content)
        if self.is_duplicate(content_hash):
            logger.info("Duplicate filing skipped: %s", content_hash)
            return True
        self.mark_seen(content_hash)
        return False

    def stats(self) -> dict:
        """Return cache statistics."""
        try:
            keys = _redis.keys("dedup:*")
            return {"total_entries": len(keys), "ttl_hours": config.DEDUP_TTL_HOURS}
        except Exception:
            return {"total_entries": 0, "ttl_hours": config.DEDUP_TTL_HOURS}


# Singleton
dedup_cache = DedupCache()
