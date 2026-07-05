"""Async database engine and session factory.

Uses asyncpg (Postgres) when DATABASE_URL is set, otherwise falls back to
aiosqlite for zero-config local development.
"""

from __future__ import annotations

import os
import logging

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger("opportunity_radar.database")


def _build_url(raw: str) -> str:
    """Convert a standard postgres:// URL to the SQLAlchemy asyncpg driver URL."""
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw.startswith("postgresql://") and "+asyncpg" not in raw:
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


_raw_url = os.getenv("DATABASE_URL", "")

if _raw_url:
    DATABASE_URL = _build_url(_raw_url)
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    logger.info("Database engine created: PostgreSQL (asyncpg)")
else:
    # Local dev — no setup required
    DATABASE_URL = "sqlite+aiosqlite:///./data/dev.db"
    engine = create_async_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
    logger.info("Database engine created: SQLite (aiosqlite) — local dev mode")


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields an async session."""
    async with AsyncSessionLocal() as session:
        yield session


async def create_tables():
    """Create all tables on startup (idempotent)."""
    from infra import db_models  # noqa: F401 — registers models with Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")
