"""Async PostgreSQL/SQLite user store with JWT authentication support."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from infra.auth import hash_password, verify_password
from infra.database import AsyncSessionLocal
from infra.db_models import FeedbackModel, UserModel
from models import UserProfile

logger = logging.getLogger("opportunity_radar.user_store")


class UserStore:
    """Async user profile management backed by SQLAlchemy.

    All methods create their own session so they can be called from
    anywhere in the codebase without injecting a session dependency.
    """

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def create_user(
        self, name: str, email: str, password: str
    ) -> Optional[UserProfile]:
        """Create a new user account.  Returns None if email already exists."""
        async with AsyncSessionLocal() as db:
            existing = await db.scalar(
                select(UserModel).where(UserModel.email == email)
            )
            if existing:
                return None

            user = UserModel(
                id=uuid.uuid4().hex[:12],
                name=name,
                email=email,
                password_hash=hash_password(password),
                watchlist=[],
                sectors=[],
                notification_prefs={"push": True, "email": False, "in_app": True},
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            logger.info("User created: %s (%s)", name, email)
            return self._model_to_profile(user)

    async def authenticate(self, email: str, password: str) -> Optional[UserProfile]:
        """Verify credentials.  Returns UserProfile on success, None on failure."""
        async with AsyncSessionLocal() as db:
            user = await db.scalar(
                select(UserModel).where(UserModel.email == email)
            )
            if not user:
                return None
            if not user.password_hash or not verify_password(password, user.password_hash):
                return None
            return self._model_to_profile(user)

    async def get_user_by_email(self, email: str) -> Optional[UserProfile]:
        async with AsyncSessionLocal() as db:
            user = await db.scalar(
                select(UserModel).where(UserModel.email == email)
            )
            return self._model_to_profile(user) if user else None

    # ── Queries ───────────────────────────────────────────────────────────────

    async def get_user(self, user_id: str) -> Optional[UserProfile]:
        async with AsyncSessionLocal() as db:
            user = await db.get(UserModel, user_id)
            return self._model_to_profile(user) if user else None

    async def get_all_users(self) -> list[UserProfile]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(UserModel))
            return [self._model_to_profile(u) for u in result.scalars().all()]

    async def get_users_for_stock(self, symbol: str) -> list[UserProfile]:
        """Return users who have this symbol in their watchlist.

        Uses a JSON contains check — works on both Postgres (JSONB) and SQLite.
        """
        users = await self.get_all_users()
        return [u for u in users if symbol in u.watchlist]

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def update_watchlist(self, user_id: str, watchlist: list[str]):
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(watchlist=watchlist)
            )
            await db.commit()

    async def record_feedback(self, alert_id: str, user_id: str, action: str):
        async with AsyncSessionLocal() as db:
            feedback = FeedbackModel(
                id=uuid.uuid4().hex[:12],
                alert_id=alert_id,
                user_id=user_id,
                action=action,
            )
            db.add(feedback)
            await db.commit()

    async def get_feedback_stats(self) -> dict:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(FeedbackModel.action, func.count().label("cnt"))
                .group_by(FeedbackModel.action)
            )
            return {row.action: row.cnt for row in result.all()}

    # ── Seeding ───────────────────────────────────────────────────────────────

    async def seed_demo_users(self):
        """Insert demo users if the table is empty (idempotent)."""
        async with AsyncSessionLocal() as db:
            count = await db.scalar(select(func.count()).select_from(UserModel))
            if count and count > 0:
                return

        demo = [
            ("Aarushi Sharma", "aarushi@example.com", "demo1234",
             ["RELIANCE", "INFY", "HDFCBANK", "TATAMOTORS", "ITC"],
             ["Technology", "Banking", "FMCG"]),
            ("Rahul Verma", "rahul@example.com", "demo1234",
             ["ADANIENT", "BAJFINANCE", "SWIGGY", "ZOMATO"],
             ["Infrastructure", "Financial Services", "Technology"]),
        ]
        for name, email, pw, watchlist, sectors in demo:
            async with AsyncSessionLocal() as db:
                existing = await db.scalar(
                    select(UserModel).where(UserModel.email == email)
                )
                if not existing:
                    db.add(UserModel(
                        id=uuid.uuid4().hex[:12],
                        name=name,
                        email=email,
                        password_hash=hash_password(pw),
                        watchlist=watchlist,
                        sectors=sectors,
                        notification_prefs={"push": True, "email": False, "in_app": True},
                    ))
                    await db.commit()

        logger.info("Demo users seeded")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _model_to_profile(user: UserModel) -> UserProfile:
        watchlist = user.watchlist if isinstance(user.watchlist, list) else json.loads(user.watchlist or "[]")
        sectors = user.sectors if isinstance(user.sectors, list) else json.loads(user.sectors or "[]")
        prefs = user.notification_prefs if isinstance(user.notification_prefs, dict) else json.loads(user.notification_prefs or "{}")
        return UserProfile(
            id=user.id,
            name=user.name,
            email=user.email,
            watchlist=watchlist,
            sectors=sectors,
            notification_prefs=prefs,
        )


# Singleton
user_store = UserStore()
