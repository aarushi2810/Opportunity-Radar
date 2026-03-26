"""Central configuration for Opportunity Radar."""

import os

# ── API Keys ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── Polling & Timing ─────────────────────────────────────────────────────────
FILING_POLL_INTERVAL_SEC = int(os.getenv("FILING_POLL_INTERVAL", "900"))  # 15 min
DATA_FRESHNESS_MAX_MS = 4 * 60 * 60 * 1000  # 4 hours

# ── Thresholds ────────────────────────────────────────────────────────────────
NOVELTY_THRESHOLD = float(os.getenv("NOVELTY_THRESHOLD", "0.5"))
SIGNAL_THRESHOLD = float(os.getenv("SIGNAL_THRESHOLD", "0.65"))
HUMAN_REVIEW_THRESHOLD = float(os.getenv("HUMAN_REVIEW_THRESHOLD", "0.7"))

# ── Retry Policy ──────────────────────────────────────────────────────────────
RETRY_DELAYS = [2, 8, 30]  # exponential backoff seconds
MAX_RETRIES = 3

# ── Dedup ─────────────────────────────────────────────────────────────────────
DEDUP_TTL_HOURS = 48

# ── Server ────────────────────────────────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# ── Demo Mode ─────────────────────────────────────────────────────────────────
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
DEMO_POLL_INTERVAL_SEC = 10  # faster polling for demo
