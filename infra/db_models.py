"""SQLAlchemy ORM models — maps Python classes to database tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from infra.database import Base

try:
    from sqlalchemy.dialects.postgresql import JSONB
    _JSON = JSONB
except ImportError:
    from sqlalchemy import JSON
    _JSON = JSON


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    watchlist: Mapped[dict] = mapped_column(_JSON, nullable=False, default=list)
    sectors: Mapped[dict] = mapped_column(_JSON, nullable=False, default=list)
    notification_prefs: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AlertModel(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enriched_signal_id: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    risk_flags: Mapped[dict] = mapped_column(_JSON, nullable=False, default=list)
    historical_base_rate: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    tags: Mapped[dict] = mapped_column(_JSON, nullable=False, default=list)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    filing_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dimension_scores: Mapped[dict] = mapped_column(_JSON, nullable=True)
    data_freshness_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class FeedbackModel(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    correct: Mapped[int] = mapped_column(Integer, nullable=False)
    accuracy_pct: Mapped[float] = mapped_column(Float, nullable=False)
    results_json: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
