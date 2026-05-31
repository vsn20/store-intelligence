# Purplle Store Intelligence

End-to-end retail analytics pipeline: raw CCTV footage → structured events → live store metrics API.

---

## Setup (5 commands)

```bash
git clone <your-repo-url>
cd store-intelligence
cp .env.example .env          # optional — defaults already in docker-compose
docker compose up --build
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "ok", "last_event_per_store": {}, "stale_feeds": [], "checked_at": "..."}
```

---

## Run the detection pipeline

### Option A — one command (processes all clips for one store)

```bash
cd pipeline
bash run.sh ../data/clips/ ../data/store_layout.json ../output/
```

`run.sh` iterates over every `.mp4` in the clips directory, runs `detect.py` then
`tracker.py` for each camera, and writes a combined `events.jsonl` to the output
directory.

### Option B — run manually per camera

```bash
cd pipeline
pip install ultralytics opencv-python

# Step 1 — detection: video → per-frame bounding boxes
python detect.py \
  --video ../data/clips/STORE_BLR_002_CAM_ENTRY_01.mp4 \
  --store_id STORE_BLR_002 \
  --camera_id CAM_ENTRY_01 \
  --clip_start_iso 2026-03-03T14:00:00Z \
  --output ../output/detections_CAM_ENTRY_01.jsonl

# Step 2 — tracking: bounding boxes → structured events
python tracker.py \
  --detections ../output/detections_CAM_ENTRY_01.jsonl \
  --store_layout ../data/store_layout.json \
  --camera_id CAM_ENTRY_01 \
  --output ../output/events_CAM_ENTRY_01.jsonl
```

Repeat for each camera angle (`CAM_ENTRY_01`, `CAM_FLOOR_01`, `CAM_BILLING_01`).

---

## Feed events into the API

```bash
# Ingest a batch of events (up to 500 per request)
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d @output/events_batch.json
```

Expected response:
```json
{"accepted": 47, "rejected": 0, "errors": []}
```

---

## Live dashboard

Open **http://localhost:8000/dashboard** after starting the API.

The dashboard polls `/stores/{id}/metrics` every 5 seconds and updates visitor
count, conversion rate, queue depth, and abandonment rate in real time.

---

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/events/ingest` | Ingest up to 500 events (idempotent by `event_id`) |
| `GET`  | `/stores/{id}/metrics` | Today's unique visitors, conversion rate, queue depth |
| `GET`  | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase funnel with drop-off % |
| `GET`  | `/stores/{id}/heatmap` | Zone visit frequency + avg dwell normalised 0–100 |
| `GET`  | `/stores/{id}/anomalies` | Active anomalies: queue spike, dead zone, conversion drop |
| `GET`  | `/health` | Service status + STALE_FEED warning if >10 min lag |

Interactive docs: **http://localhost:8000/docs**

---

## Run tests

```bash
docker compose exec api pytest tests/ --cov=app --cov-report=term-missing
```

Expected: **43 passed, 83% coverage**.

Test files:
- `tests/test_metrics.py` — metrics, funnel, heatmap, health endpoints
- `tests/test_anomalies.py` — queue spike, dead zone, conversion drop anomalies
- `tests/test_pipeline.py` — detection schema compliance, staff heuristic, zone events, billing events

---

## Repository structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py          # YOLOv8m detection → JSONL bounding boxes
│   ├── tracker.py         # ByteTrack + direction inference → structured events
│   ├── emit.py            # Event schema helpers
│   └── run.sh             # One command: clips directory → events.jsonl
├── app/
│   ├── main.py            # FastAPI entrypoint + request logging middleware
│   ├── models.py          # SQLAlchemy ORM + Pydantic event schema
│   ├── ingenstion.py      # Ingest endpoint (idempotent, partial success)
│   ├── metrics.py         # Real-time metrics endpoint
│   ├── funnel.py          # Conversion funnel endpoint
│   ├── heatmap.py         # Zone heatmap endpoint
│   ├── anomalies.py       # Anomaly detection (queue spike, dead zone, conversion drop)
│   ├── health.py          # Health + STALE_FEED endpoint
│   └── database.py        # SQLAlchemy engine + session factory
├── tests/
│   ├── conftest.py        # SQLite override for test isolation
│   ├── test_metrics.py
│   ├── test_anomalies.py
│   └── test_pipeline.py
├── dashboard/
│   └── index.html         # Live metrics dashboard (served at /dashboard)
├── docs/
│   ├── DESIGN.md
│   └── CHOICES.md
├── docker-compose.yml     # PostgreSQL 15 + API — single command startup
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Environment variables

All variables have defaults in `docker-compose.yml`. Override in `.env` if needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://...` | PostgreSQL connection string |
| `PORT` | `8000` | API port |

---

## Notes

- Video files and dataset are **not included** in this repository per challenge rules.
- Place clips in `data/clips/` and `store_layout.json` in `data/` before running the pipeline.
- The pipeline can be run on CPU — no GPU required. Processing time: ~2–4 minutes per 20-minute clip on a modern CPU.
- All API responses exclude `is_staff=True` events from customer metrics.