"""SQLite-backed user profile store."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from models import UserProfile

logger = logging.getLogger("opportunity_radar.user_store")

DB_PATH = Path(__file__).parent.parent / "data" / "users.db"


class UserStore:
    """SQLite-backed user profile management."""

    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._seed_demo_users()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT DEFAULT '',
                watchlist TEXT DEFAULT '[]',
                sectors TEXT DEFAULT '[]',
                notification_prefs TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self._conn.commit()

    def _seed_demo_users(self):
        """Seed demo users if table is empty."""
        count = self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return

        demo_users = [
            UserProfile(
                id="user_001",
                name="Aarushi Sharma",
                email="aarushi@example.com",
                watchlist=["RELIANCE", "INFY", "HDFCBANK", "TATAMOTORS", "ITC"],
                sectors=["Technology", "Banking", "FMCG"],
            ),
            UserProfile(
                id="user_002",
                name="Rahul Verma",
                email="rahul@example.com",
                watchlist=["ADANIENT", "BAJFINANCE", "SWIGGY", "ZOMATO"],
                sectors=["Infrastructure", "Financial Services", "Technology"],
            ),
        ]
        for user in demo_users:
            self._insert_user(user)
        logger.info(f"Seeded {len(demo_users)} demo users")

    def _insert_user(self, user: UserProfile):
        self._conn.execute(
            "INSERT OR IGNORE INTO users (id, name, email, watchlist, sectors, notification_prefs) VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.name, user.email,
             json.dumps(user.watchlist), json.dumps(user.sectors),
             json.dumps(user.notification_prefs)),
        )
        self._conn.commit()

    def get_all_users(self) -> list[UserProfile]:
        rows = self._conn.execute("SELECT * FROM users").fetchall()
        return [self._row_to_user(r) for r in rows]

    def get_user(self, user_id: str) -> UserProfile | None:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def get_users_for_stock(self, symbol: str) -> list[UserProfile]:
        """Get all users who have this stock in their watchlist."""
        users = self.get_all_users()
        return [u for u in users if symbol in u.watchlist]

    def update_watchlist(self, user_id: str, watchlist: list[str]):
        self._conn.execute(
            "UPDATE users SET watchlist = ? WHERE id = ?",
            (json.dumps(watchlist), user_id),
        )
        self._conn.commit()

    def record_feedback(self, alert_id: str, user_id: str, action: str):
        import uuid
        self._conn.execute(
            "INSERT INTO feedback (id, alert_id, user_id, action) VALUES (?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], alert_id, user_id, action),
        )
        self._conn.commit()

    def get_feedback_stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT action, COUNT(*) as cnt FROM feedback GROUP BY action"
        ).fetchall()
        return {r["action"]: r["cnt"] for r in rows}

    @staticmethod
    def _row_to_user(row) -> UserProfile:
        return UserProfile(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            watchlist=json.loads(row["watchlist"]),
            sectors=json.loads(row["sectors"]),
            notification_prefs=json.loads(row["notification_prefs"]),
        )


# Singleton
user_store = UserStore()
