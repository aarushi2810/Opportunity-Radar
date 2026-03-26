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
- [Optional] Docker

### Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Copy the env template and set your values:
```bash
cp .env.example .env
# Edit .env to add your GEMINI_API_KEY (optional)
```

### Running Locally

```bash
python main.py
```

The services will be available at:
- 📊 **Dashboard**: http://localhost:8000
- 📡 **REST API**: http://localhost:8000/api/alerts
- 🔴 **SSE Stream**: http://localhost:8000/api/alerts/stream
- 🩺 **Health**: http://localhost:8000/api/pipeline/status

## 🐳 Deployment

### Docker

```bash
docker build -t opportunity-radar .
docker run -p 8000:8000 -e GEMINI_API_KEY="your_key" opportunity-radar
```

### Render / Railway / Heroku

1. Push the repo to GitHub.
2. Connect the repo to Render / Railway / Heroku.
3. The `Procfile` will be auto-detected. Set environment variables (`GEMINI_API_KEY`, `DEMO_MODE=false`) in the platform's dashboard.
4. Deploy.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(empty)* | Google Gemini API key for LLM classification |
| `PORT` | `8000` | Server port |
| `HOST` | `0.0.0.0` | Server bind address |
| `DEMO_MODE` | `true` | Use sample filings (`true`) or real sources (`false`) |
| `FILING_POLL_INTERVAL` | `900` | Seconds between filing polls |
| `SIGNAL_THRESHOLD` | `0.65` | Minimum importance score to generate an alert |
| `HUMAN_REVIEW_THRESHOLD` | `0.7` | Confidence below this triggers human review |

## 🏗️ Architecture

1. **`agents/filing_watcher.py`**: Polls data sources for new corporate filings.
2. **`agents/orchestrator.py`**: The central coordinator managing deduplication and routing.
3. **`agents/signal_classifier.py`**: Scores the event on 5 dimensions (Magnitude, Credibility, Timing, Momentum, Historical Match).
4. **`agents/context_enricher.py`**: Enriches signals with live market data (Price, P/E, EPS, Market Cap, Peers).
5. **`agents/alert_composer.py`**: Translates the enriched data into an actionable alert sequence.

## 💾 Infrastructure & Data

- **`infra/user_store.py`**: SQLite database tracking user profiles, sector preferences, and recorded feedback.
- **`infra/market_data.py`**: Mocks live market fundamentals, price movements, and peer comparisons for enrichment.
