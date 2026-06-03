# Store Intelligence System — Purplle Tech Challenge 2026

End-to-end CCTV analytics pipeline. From raw footage to live store metrics.

## Live Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service status |
| GET | `/docs` | Interactive API docs |
| GET | `/dashboard` | Live metrics dashboard |
| POST | `/events/ingest` | Ingest detection events |
| GET | `/stores/{id}/metrics` | Visitors, conversion, dwell, queue |
| GET | `/stores/{id}/funnel` | Entry to Purchase funnel |
| GET | `/stores/{id}/heatmap` | Zone visit heatmap |
| GET | `/stores/{id}/anomalies` | Active anomalies |

## Stack
- Detection: YOLOv8m + ByteTrack
- API: FastAPI + SQLAlchemy
- Storage: SQLite (demo) / PostgreSQL (production)
- Container: Docker

## Repository
github.com/vsn20/store-intelligence

##checking