"""Redis-backed deduplication cache with content hash + TTL."""

from __future__ import annotations

import hashlib
import json
import logging
import time

import fakeredis

import config

logger = logging.getLogger("opportunity_radar.dedup")


class DedupCache:
    """Content-hash dedup using Redis (fakeredis for local dev)."""

    def __init__(self):
        self._redis = fakeredis.FakeRedis(decode_responses=True)
        self._ttl_seconds = config.DEDUP_TTL_HOURS * 3600
        logger.info(f"DedupCache initialized (TTL: {config.DEDUP_TTL_HOURS}h)")

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute content hash for dedup."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def is_duplicate(self, content_hash: str) -> bool:
        """Check if content hash already exists."""
        key = f"dedup:{content_hash}"
        return self._redis.exists(key) == 1

    def mark_seen(self, content_hash: str):
        """Mark a content hash as seen with TTL."""
        key = f"dedup:{content_hash}"
        self._redis.setex(key, self._ttl_seconds, str(time.time()))
        logger.debug(f"Marked seen: {content_hash}")

    def check_and_mark(self, content: str) -> bool:
        """Check if content is duplicate. If not, mark as seen. Returns True if duplicate."""
        content_hash = self.compute_hash(content)
        if self.is_duplicate(content_hash):
            logger.info(f"Duplicate detected: {content_hash}")
            return True
        self.mark_seen(content_hash)
        return False

    def stats(self) -> dict:
        """Return cache stats."""
        keys = self._redis.keys("dedup:*")
        return {"total_entries": len(keys), "ttl_hours": config.DEDUP_TTL_HOURS}


# Singleton
dedup_cache = DedupCache()
