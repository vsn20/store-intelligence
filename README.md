# Purplle Store Intelligence

## Setup (5 commands)

```bash
git clone <your-repo-url>
cd store-intelligence
cp .env.example .env          # optional — defaults already in docker-compose
docker compose up --build
curl http://localhost:8000/health
```

## Run detection pipeline

```bash
cd pipeline
pip install ultralytics opencv-python
python detect.py --video ../data/clips/STORE_BLR_002_CAM_ENTRY_01.mp4 \
  --store_id STORE_BLR_002 --camera_id CAM_ENTRY_01 \
  --clip_start_iso 2026-03-03T14:00:00Z \
  --output ../output/detections.jsonl
python tracker.py --detections ../output/detections.jsonl \
  --store_layout ../data/store_layout.json --output ../output/events.jsonl
```

## Feed events into API

```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d @output/events_batch.json
```

## Run tests

```bash
docker compose exec api pytest tests/ --cov=app --cov-report=term-missing
```

## API docs

Open http://localhost:8000/docs