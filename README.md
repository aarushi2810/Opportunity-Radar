# Opportunity Radar 🔭

Multi-Agent Intelligence System for Indian Market Signals. 

This system monitors corporate filings, processes them through a pipeline of intelligent agents to classify their impact, enriches them with market context, and surfaces actionable alerts to users in real-time.

## 🌟 Features

- **Multi-Agent Pipeline**: Specialized agents for watching filings, classifying signals, enriching context, and composing alerts. 
- **LLM-Powered Analysis**: Uses Gemini 2.0 to accurately classify signals and predict their market impact (with a robust rule-based fallback).
- **Real-Time Dashboard**: A sleek dark-mode UI with live pipeline visualization and an SSE-powered alert feed.
- **Async Message Bus**: In-memory message bus (`message_bus.py`) coordinating all agent activity based on a pub/sub pattern.

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- [Optional] Google Gemini API Key

### Installation

1. Create a virtual environment and install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```
*(Note: If `pip install -e .` fails due to multiple top-level packages, simply install the requirements manually or using a virtual environment package manager.)*

2. (Optional) Set up your Gemini API key for LLM-powered classification:
```bash
export GEMINI_API_KEY="your_api_key_here"
```

### Running the Application

Start the FastAPI server and agent orchestrator:
```bash
python main.py
```

The services will be available at:
- 📊 **Dashboard**: http://localhost:8000
- 📡 **REST API**: http://localhost:8000/api/alerts
- 🔴 **SSE Stream**: http://localhost:8000/api/alerts/stream
- 🩺 **Health**: http://localhost:8000/api/pipeline/status

## 🏗️ Architecture

1. **`agents/filing_watcher.py`**: Polls data sources for new corporate filings.
2. **`agents/orchestrator.py`**: The central coordinator managing deduplication and routing.
3. **`agents/signal_classifier.py`**: Scores the event on 5 dimensions (Magnitude, Credibility, Timing, Momentum, Historical Match).
4. **`agents/context_enricher.py`**: Enriches signals with live market data (Price, P/E, EPS, Market Cap, Peers).
5. **`agents/alert_composer.py`**: Translates the enriched data into an actionable alert sequence.

## 💾 Infrastructure & Data

- **`infra/user_store.py`**: SQLite database tracking user profiles, sector preferences, and recorded feedback.
- **`infra/market_data.py`**: Mocks live market fundamentals, price movements, and peer comparisons for enrichment.
