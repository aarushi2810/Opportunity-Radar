"""Entry point for Opportunity Radar — starts orchestrator + FastAPI server."""

import asyncio
import logging
import sys

import uvicorn

import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-35s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Suppress noisy loggers
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def main():
    print(r"""
  ╔═══════════════════════════════════════════════════════════╗
  ║                                                           ║
  ║        🔭  O P P O R T U N I T Y   R A D A R  🔭        ║
  ║                                                           ║
  ║   Multi-Agent Intelligence for Indian Market Signals      ║
  ║                                                           ║
  ╚═══════════════════════════════════════════════════════════╝
    """)

    print(f"  📊 Dashboard:    http://localhost:{config.PORT}")
    print(f"  📡 API:          http://localhost:{config.PORT}/api/alerts")
    print(f"  🔴 SSE Stream:   http://localhost:{config.PORT}/api/alerts/stream")
    print(f"  🩺 Health:       http://localhost:{config.PORT}/api/pipeline/status")
    print(f"  🧪 Demo Mode:    {'ON' if config.DEMO_MODE else 'OFF'}")
    print(f"  🤖 Gemini:       {'Configured' if config.GEMINI_API_KEY else 'Not set (rule-based mode)'}")
    print()

    uvicorn.run(
        "server:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
