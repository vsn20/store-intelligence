
# Store Intelligence System — Purplle Tech Challenge 2026

End-to-end CCTV analytics pipeline: raw footage → structured events → live store metrics.

**North Star Metric**: Offline Store Conversion Rate = Visitors who purchased ÷ Total unique visitors

## Quickstart (5 commands)

```bash

git clone https://github.com/vsn20/store-intelligence.git

cd store-intelligence

docker compose up --build -d

cd pipeline && python ingest.py

open http://localhost:8000/dashboard

```

Set date to **2026-04-10** in the dashboard to see Brigade Road store data.

> YOLOv8m model weights are downloaded automatically by Ultralytics on first run.

---

## Running the Detection Pipeline

**Stage 1 — Detection** (YOLOv8m per camera):

```bash

python detect.py \

  --video "../data/clips/CAM 1.mp4" \

  --store_id ST1008 \

  --camera_id "CAM 1" \

  --clip_start_iso "2026-04-10T12:00:00+00:00" \

  --output "../output/detections_CAM_1.jsonl"

```

**Stage 2 — Tracking + Event emission**:

```bash

python tracker.py \

  --detections "../output/detections_CAM_1.jsonl" \

  --store_layout "../data/store_layout.json" \

  --camera_id "CAM 1" \

  --output "../output/events_CAM_1.jsonl"

```

**Or run all cameras in one shot:**

```bash

cd pipeline && bash run.sh

```

**Stage 3 — Ingest events into the API:**

```bash

cd pipeline && python ingest.py

```

**Stage 4 — Real-time replay (watch dashboard update live):**

```bash

cd pipeline && python replay.py --speed 3.0

# Replays events with real-time gaps — open dashboard simultaneously to see live updates

# Use --speed 0 for instant batch, --speed 1.0 for real time, --speed 5.0 for 5x faster

```

---

## API Endpoints

| Method | Endpoint | Description |

|--------|----------|-------------|

| GET | /health | Service status + stale feed warnings |

| GET | /docs | Interactive Swagger docs |

| GET | /dashboard | Live metrics dashboard |

| POST | /events/ingest | Ingest up to 500 events per batch — idempotent by event_id |

| GET | /stores/{id}/metrics | Visitors, conversion rate, dwell, queue depth |

| GET | /stores/{id}/funnel | Entry → Zone → Billing → Purchase funnel (POS-correlated) |

| GET | /stores/{id}/heatmap | Zone visit frequency + avg dwell (0–100 score) |

| GET | /stores/{id}/anomalies | Active anomalies: queue spike, dead zone, conversion drop |

| GET | /stores/{id}/stream | SSE real-time event stream — subscribe for live updates |

All metric endpoints accept optional ?date=YYYY-MM-DD. Default is today UTC.

### Example requests

```bash

curl http://localhost:8000/health

curl "http://localhost:8000/stores/ST1008/metrics?date=2026-04-10"

curl "http://localhost:8000/stores/ST1008/funnel?date=2026-04-10"

curl "http://localhost:8000/stores/ST1008/anomalies"

# SSE stream — connect and receive live events as they are ingested

curl -N http://localhost:8000/stores/ST1008/stream

```

---

## Real-time Dashboard

The dashboard at /dashboard connects to the SSE endpoint (/stores/{id}/stream) and updates instantly as events flow in. Features:

- Live visitor count, conversion rate, queue depth, abandonment rate

- Conversion funnel bar chart (Chart.js) with drop-off percentages

- Queue depth over time line chart — updates on every BILLING_QUEUE_JOIN event

- Zone heatmap with proportional dwell bars

- Live event list colour-coded by event type (ENTRY, ZONE_ENTER, BILLING_QUEUE_JOIN, etc.)

- SSE status badge showing connection state (live / polling fallback)

To see genuinely live updates, run replay.py in one terminal while the dashboard is open in the browser.

---

## Project Structure

store-intelligence/ ├── pipeline/ │ ├── detect.py # YOLOv8m + ByteTrack — bounding boxes → JSONL │ ├── tracker.py # Direction inference, zone mapping, Re-ID, event emission │ ├── ingest.py # Batch POST events.jsonl → API │ ├── replay.py # Real-time event replay with configurable speed │ └── run.sh # One-command: all cameras → events.jsonl ├── app/ │ ├── main.py # FastAPI entrypoint, middleware, routers │ ├── models.py # Pydantic event schema + SQLAlchemy ORM │ ├── ingenstion.py # POST /events/ingest — validate, dedup, store │ ├── metrics.py # GET /stores/{id}/metrics │ ├── funnel.py # GET /stores/{id}/funnel + POS correlation │ ├── heatmap.py # GET /stores/{id}/heatmap │ ├── anomalies.py # GET /stores/{id}/anomalies │ ├── health.py # GET /health │ ├── stream.py # GET /stores/{id}/stream — SSE real-time events │ └── database.py # SQLAlchemy engine, session, init_db ├── tests/ │ ├── conftest.py # SQLite test DB fixture │ ├── test_pipeline.py # Detection + tracker unit tests │ ├── test_metrics.py # API endpoint tests │ └── test_anomalies.py # Anomaly detection tests ├── dashboard/ │ └── index.html # Live dashboard — SSE + Chart.js funnel + queue charts ├── data/ │ ├── store_layout.json # Zone definitions, camera roles, open hours │ └── pos_transactions.csv ├── docs/ │ ├── DESIGN.md # Architecture + AI-assisted decisions │ └── CHOICES.md # 5 engineering decisions with full reasoning ├── Dockerfile # Local dev — port 8000 ├── Dockerfile.hf # HuggingFace deploy — port 7860 + SQLite ├── docker-compose.yml └── requirements.txt

---

## Running Tests

```bash

docker compose exec api pytest tests/ --cov=app --cov-report=term-missing

# 43 tests passing, 79% coverage

```

---

## Configuration

| Variable | Default | Description |

|---|---|---|

| DATABASE_URL | sqlite:///./data/store_intelligence.db | Database connection string |

| POS_CSV_PATH | /app/data/pos_transactions.csv | Path to POS transactions file |

| PORT | 8000 | API server port |

PostgreSQL is used in Docker Compose. SQLite is the automatic fallback for local dev and HuggingFace.

---

## POS Transaction Correlation

Conversion rate is computed by correlating billing zone presence with POS transaction timestamps. A visitor counts as converted if their BILLING_QUEUE_JOIN event falls within 5 minutes before any POS transaction for the same store. The funnel response includes pos_correlated: true/false so callers know which method produced the conversion count.

---

## Live Demo

- **Dashboard:** https://vsn20-store-intelligence.hf.space/dashboard

- **API Docs:** https://vsn20-store-intelligence.hf.space/docs

- **Health:** https://vsn20-store-intelligence.hf.space/health

## Repository

- **GitHub:** https://github.com/vsn20/store-intelligence

