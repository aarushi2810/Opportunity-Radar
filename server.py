"""FastAPI server — REST API + SSE for real-time alerts."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from agents.orchestrator import OrchestratorAgent
from infra.user_store import user_store
from models import FeedbackAction

logger = logging.getLogger("opportunity_radar.server")

# Global orchestrator instance
orchestrator = OrchestratorAgent()

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start orchestrator on app startup, stop on shutdown."""
    logger.info("🌐 Starting Opportunity Radar server...")
    await orchestrator.start()
    yield
    await orchestrator.stop()


app = FastAPI(
    title="Opportunity Radar",
    description="Multi-agent intelligence system for Indian market signals",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the dashboard."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text())


# ── API Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    """Get latest alerts."""
    alerts = orchestrator.get_alerts(limit)
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
async def approve_alert(alert_id: str):
    """Approve a held alert."""
    success = orchestrator.approve_alert(alert_id)
    return JSONResponse(content={"success": success})


@app.get("/api/alerts/stream")
async def stream_alerts(request: Request):
    """SSE endpoint for real-time alert streaming."""

    async def event_generator():
        last_count = 0
        while True:
            if await request.is_disconnected():
                break
            alerts = orchestrator.get_alerts(50)
            current_count = len(alerts)
            if current_count > last_count:
                new_alerts = alerts[:current_count - last_count]
                for alert in new_alerts:
                    yield {
                        "event": "alert",
                        "data": json.dumps(alert.model_dump(mode="json"), default=str),
                    }
                last_count = current_count

            # Also send pipeline status periodically
            status = orchestrator.get_pipeline_status()
            yield {
                "event": "status",
                "data": json.dumps(status.model_dump(mode="json"), default=str),
            }

            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())


@app.get("/api/pipeline/status")
async def pipeline_status():
    """Get agent pipeline health status."""
    status = orchestrator.get_pipeline_status()
    return JSONResponse(content=status.model_dump(mode="json"))


@app.get("/api/users")
async def get_users():
    """Get all users."""
    users = user_store.get_all_users()
    return JSONResponse(content={
        "users": [u.model_dump(mode="json") for u in users],
    })


@app.post("/api/feedback")
async def submit_feedback(request: Request):
    """Submit user feedback on an alert."""
    data = await request.json()
    alert_id = data.get("alert_id", "")
    user_id = data.get("user_id", "user_001")
    action = data.get("action", "DISMISS")

    try:
        FeedbackAction(action)
    except ValueError:
        return JSONResponse(content={"error": f"Invalid action: {action}"}, status_code=400)

    user_store.record_feedback(alert_id, user_id, action)
    logger.info(f"📝 Feedback recorded: alert={alert_id}, action={action}")
    return JSONResponse(content={"success": True})


@app.post("/api/watchlist")
async def update_watchlist(request: Request):
    """Update user watchlist."""
    data = await request.json()
    user_id = data.get("user_id", "user_001")
    watchlist = data.get("watchlist", [])
    user_store.update_watchlist(user_id, watchlist)
    return JSONResponse(content={"success": True, "watchlist": watchlist})


@app.get("/api/feedback/stats")
async def feedback_stats():
    """Get feedback statistics."""
    stats = user_store.get_feedback_stats()
    return JSONResponse(content=stats)
