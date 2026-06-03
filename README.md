# Store Intelligence System — Purplle Tech Challenge 2026

End-to-end CCTV analytics pipeline: raw footage → structured events → live store metrics.

## Quickstart (5 commands)

```bash
# 1. Clone the repo
git clone https://github.com/vsn20/store-intelligence.git && cd store-intelligence

# 2. Start the API + database
docker compose up --build -d

# 3. Run the detection pipeline against your clips
#    Place video files at data/clips/CAM 1.mp4, CAM 2.mp4 … CAM 5.mp4
cd pipeline && bash run.sh

# 4. Ingest the generated events into the API
python ingest.py

# 5. Check live metrics
curl http://localhost:8000/stores/ST1008/metrics
```

The API is now live at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`. Live dashboard at `http://localhost:8000/dashboard`.

> **Note:** YOLOv8m model weights (`yolov8m.pt`) are downloaded automatically by Ultralytics on first run. No manual download needed.

---

## Running the Detection Pipeline

The pipeline has two stages. Run them from the `pipeline/` directory:

**Stage 1 — Detection** (YOLOv8m per camera):
```bash
python detect.py \
  --video "../data/clips/CAM 1.mp4" \
  --store_id ST1008 \
  --camera_id "CAM 1" \
  --clip_start_iso "2026-04-10T12:00:00+00:00" \
  --output "../output/detections_CAM_1.jsonl"
```

**Stage 2 — Tracking + Event emission** (zone mapping, direction inference, Re-ID):
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
# Output: ../output/events.jsonl (all cameras combined)
```

**Stage 3 — Ingest events into the API:**
```bash
cd pipeline && python ingest.py
# Reads ../output/events.jsonl, POSTs to http://localhost:8000/events/ingest in batches of 250
```

Event output format is JSONL — one event per line, conforming to the schema in `app/models.py`.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service status + stale feed warnings |
| GET | `/docs` | Interactive Swagger docs |
| GET | `/dashboard` | Live metrics dashboard |
| POST | `/events/ingest` | Ingest up to 500 events per batch |
| GET | `/stores/{id}/metrics` | Visitors, conversion rate, dwell, queue depth |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase funnel |
| GET | `/stores/{id}/heatmap` | Zone visit frequency + avg dwell (0–100 score) |
| GET | `/stores/{id}/anomalies` | Active anomalies (queue spike, dead zone, conversion drop) |

All metric endpoints accept an optional `?date=YYYY-MM-DD` query parameter. Default is today UTC.

### Example requests

```bash
# Health check
curl http://localhost:8000/health

# Today's metrics for ST1008
curl http://localhost:8000/stores/ST1008/metrics

# Metrics for a specific date
curl "http://localhost:8000/stores/ST1008/metrics?date=2026-04-10"

# Conversion funnel
curl "http://localhost:8000/stores/ST1008/funnel?date=2026-04-10"

# Zone heatmap
curl "http://localhost:8000/stores/ST1008/heatmap?date=2026-04-10"

# Active anomalies
curl http://localhost:8000/stores/ST1008/anomalies

# Ingest a batch of events
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"events": [...]}'
```

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py          # YOLOv8m + ByteTrack — bounding boxes → JSONL
│   ├── tracker.py         # Direction inference, zone mapping, Re-ID, event emission
│   ├── emit.py            # Event schema helpers
│   ├── ingest.py          # Batch POST events.jsonl → API
│   └── run.sh             # One-command: all cameras → events.jsonl
├── app/
│   ├── main.py            # FastAPI entrypoint, middleware, routers
│   ├── models.py          # Pydantic event schema + SQLAlchemy ORM
│   ├── ingenstion.py      # POST /events/ingest — validate, dedup, store
│   ├── metrics.py         # GET /stores/{id}/metrics
│   ├── funnel.py          # GET /stores/{id}/funnel + POS correlation
│   ├── heatmap.py         # GET /stores/{id}/heatmap
│   ├── anomalies.py       # GET /stores/{id}/anomalies
│   ├── health.py          # GET /health
│   └── database.py        # SQLAlchemy engine, session, init_db
├── tests/
│   ├── conftest.py        # SQLite test DB fixture
│   ├── test_pipeline.py   # Detection + tracker unit tests
│   ├── test_metrics.py    # API endpoint tests
│   └── test_anomalies.py  # Anomaly detection tests
├── dashboard/
│   └── index.html         # Live dashboard (polls /metrics every 5s)
├── data/
│   ├── store_layout.json  # Zone definitions, camera roles, open hours
│   └── pos_transactions.csv
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # Three key engineering decisions with full reasoning
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Running Tests

```bash
# Install dependencies (outside Docker)
pip install -r requirements.txt

# Run full test suite with coverage
pytest --cov=app --cov-report=term-missing

# Run a specific test file
pytest tests/test_metrics.py -v
```

---

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/store_intelligence.db` | Database connection string |
| `POS_CSV_PATH` | `/app/data/pos_transactions.csv` | Path to POS transactions file |
| `PORT` | `8000` | API server port |

In Docker Compose, `DATABASE_URL` is set to the PostgreSQL container. For local dev without Docker, SQLite is used automatically.

---

## POS Transaction Correlation

Conversion rate is computed by correlating billing zone presence with POS transaction timestamps. A visitor counts as converted if their `BILLING_QUEUE_JOIN` event falls within 5 minutes before any POS transaction for the same store. Place `pos_transactions.csv` at the path set by `POS_CSV_PATH`. If the file is absent, the API falls back to a no-abandon proxy (billing joins without subsequent abandons).

---

## Live Demo

- **Dashboard:** [Store Intelligence Dashboard](https://vsn20-store-intelligence.hf.space/dashboard/)
- **API Docs:** [Swagger Documentation](https://vsn20-store-intelligence.hf.space/docs)

## Repository

- **GitHub:** [store-intelligence](https://github.com/vsn20/store-intelligence)