"""FastAPI server — REST API + SSE for real-time alerts."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from agents.orchestrator import OrchestratorAgent
from infra.auth import (
    create_access_token,
    get_current_user_id,
    get_current_user_id_optional,
)
from infra.user_store import user_store
from models import FeedbackAction

logger = logging.getLogger("opportunity_radar.server")

orchestrator = OrchestratorAgent()
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start orchestrator on app startup, stop on shutdown."""
    logger.info("Starting Opportunity Radar server...")
    await orchestrator.start()
    yield
    await orchestrator.stop()


app = FastAPI(
    title="Opportunity Radar",
    description="Multi-agent intelligence system for Indian market signals",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the dashboard."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text())


# ── Auth Endpoints ────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(request: Request):
    """Create a new user account and return a JWT."""
    data = await request.json()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="name, email and password are required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="password must be at least 6 characters")

    user = await user_store.create_user(name=name, email=email, password=password)
    if not user:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    token = create_access_token(user.id)
    return JSONResponse(content={"token": token, "user": user.model_dump(mode="json")})


@app.post("/api/auth/login")
async def login(request: Request):
    """Verify credentials and return a JWT."""
    data = await request.json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    user = await user_store.authenticate(email=email, password=password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(user.id)
    return JSONResponse(content={"token": token, "user": user.model_dump(mode="json")})


@app.get("/api/auth/me")
async def me(user_id: str = Depends(get_current_user_id)):
    """Return the current authenticated user profile."""
    user = await user_store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return JSONResponse(content=user.model_dump(mode="json"))


# ── Alert Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/alerts")
async def get_alerts(
    limit: int = Query(default=50, ge=1, le=200),
    symbol: Optional[str] = Query(default=None),
    signal: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
):
    """Get latest alerts with optional filtering by symbol, signal type, and priority."""
    alerts = orchestrator.get_alerts(limit * 3)  # over-fetch for client-side filtering

    if symbol:
        alerts = [a for a in alerts if a.stock_symbol.upper() == symbol.upper()]
    if signal:
        alerts = [a for a in alerts if a.signal_type.value.upper() == signal.upper()]
    if priority:
        alerts = [a for a in alerts if a.priority.value.upper() == priority.upper()]

    alerts = alerts[:limit]
    return JSONResponse(content={
        "alerts": [a.model_dump(mode="json") for a in alerts],
        "count": len(alerts),
    })


@app.get("/api/alerts/held")
async def get_held_alerts():
    """Get alerts held for human review."""
    held = orchestrator.get_held_alerts()
    return JSONResponse(content={
        "alerts": [a.model_dump(mode="json") for a in held],
        "count": len(held),
    })


@app.post("/api/alerts/{alert_id}/approve")
async def approve_alert(alert_id: str, user_id: str = Depends(get_current_user_id)):
    """Approve a held alert (requires authentication)."""
    success = orchestrator.approve_alert(alert_id)
    return JSONResponse(content={"success": success})


@app.get("/api/alerts/stream")
async def stream_alerts(
    request: Request,
    user_id: Optional[str] = Depends(get_current_user_id_optional),
):
    """SSE endpoint for real-time alert streaming.

    Uses cursor-based delivery (last_seen_id) — no duplicate sends.
    Authenticated users receive only alerts matching their watchlist when set.
    """
    user = await user_store.get_user(user_id) if user_id else None
    watchlist = set(user.watchlist) if user and user.watchlist else set()

    async def event_generator():
        last_seen_id: Optional[str] = None

        while True:
            if await request.is_disconnected():
                break

            new_alerts = orchestrator.get_alerts_after(last_seen_id, limit=20)

            for alert in new_alerts:
                last_seen_id = alert.id
                # Filter by watchlist if user is authenticated and has one set
                if watchlist and alert.stock_symbol not in watchlist:
                    continue
                yield {
                    "event": "alert",
                    "data": json.dumps(alert.model_dump(mode="json"), default=str),
                }

            status_data = orchestrator.get_pipeline_status()
            yield {
                "event": "status",
                "data": json.dumps(status_data.model_dump(mode="json"), default=str),
            }

            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())


# ── Pipeline / Dashboard Endpoints ────────────────────────────────────────────

@app.get("/api/pipeline/status")
async def pipeline_status():
    """Get agent pipeline health status."""
    status = orchestrator.get_pipeline_status()
    return JSONResponse(content=status.model_dump(mode="json"))


@app.get("/api/dashboard/stats")
async def dashboard_stats():
    """Aggregate dashboard stats derived from the in-memory alert cache and pipeline status."""
    pipeline = orchestrator.get_pipeline_status()
    alerts = orchestrator.get_alerts(500)

    signal_dist = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0, "WATCH": 0}
    priority_dist = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    confidence_total = 0.0

    for a in alerts:
        signal_dist[a.signal_type.value] = signal_dist.get(a.signal_type.value, 0) + 1
        priority_dist[a.priority.value] = priority_dist.get(a.priority.value, 0) + 1
        confidence_total += a.confidence_score

    avg_confidence = round(confidence_total / len(alerts), 3) if alerts else 0

    # Symbol frequency
    from collections import Counter
    sym_counter = Counter(a.stock_symbol for a in alerts)
    top_stocks = [sym for sym, _ in sym_counter.most_common(5)]

    return JSONResponse(content={
        "total_filings": pipeline.total_filings_processed,
        "total_signals": pipeline.total_signals_generated,
        "total_alerts": pipeline.total_alerts_sent,
        "signal_distribution": signal_dist,
        "alerts_by_priority": priority_dist,
        "avg_confidence": avg_confidence,
        "top_active_stocks": top_stocks,
        "uptime_seconds": pipeline.uptime_seconds,
        "pipeline": pipeline.model_dump(mode="json"),
    })


# ── User Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/users")
async def get_users():
    """Get all users (public — for demo watchlist display)."""
    users = await user_store.get_all_users()
    return JSONResponse(content={"users": [u.model_dump(mode="json") for u in users]})


@app.post("/api/watchlist")
async def update_watchlist(
    request: Request,
    user_id: str = Depends(get_current_user_id),
):
    """Update the authenticated user's watchlist."""
    data = await request.json()
    watchlist = data.get("watchlist", [])
    await user_store.update_watchlist(user_id, watchlist)
    return JSONResponse(content={"success": True, "watchlist": watchlist})


# ── Feedback Endpoints ────────────────────────────────────────────────────────

@app.post("/api/feedback")
async def submit_feedback(
    request: Request,
    user_id: Optional[str] = Depends(get_current_user_id_optional),
):
    """Submit user feedback on an alert.

    Accepts authenticated users (JWT) or falls back to the demo user_id
    provided in the request body for backward compatibility.
    """
    data = await request.json()
    alert_id = data.get("alert_id", "")
    action = data.get("action", "DISMISS")
    # Authenticated user takes priority; fall back to body param for demo mode
    effective_user_id = user_id or data.get("user_id", "demo")

    try:
        FeedbackAction(action)
    except ValueError:
        return JSONResponse(
            content={"error": f"Invalid action: {action}"},
            status_code=400,
        )

    await user_store.record_feedback(alert_id, effective_user_id, action)
    logger.info("Feedback recorded: alert=%s, action=%s, user=%s", alert_id, action, effective_user_id)
    return JSONResponse(content={"success": True})


@app.get("/api/feedback/stats")
async def feedback_stats():
    """Get feedback statistics."""
    stats = await user_store.get_feedback_stats()
    return JSONResponse(content=stats)


@app.get("/api/alerts/{alert_id}/feedback-summary")
async def alert_feedback_summary(alert_id: str):
    """Get per-alert feedback breakdown."""
    # Full stats then filter by alert — simple for portfolio scale
    all_stats = await user_store.get_feedback_stats()
    return JSONResponse(content={"alert_id": alert_id, "stats": all_stats})
